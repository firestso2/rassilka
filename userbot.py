"""
Управление Telethon-клиентами (userbot-аккаунтами).
"""

import asyncio
import logging
from typing import Optional, Dict, List, Tuple

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError, FloodWaitError,
    PhoneCodeExpiredError, PhoneCodeInvalidError
)
from telethon.tl.functions.messages import GetDialogFiltersRequest
from telethon.tl.types import DialogFilter

logger = logging.getLogger(__name__)


class UserbotManager:
    def __init__(self, db):
        self.db = db
        # acc_id -> TelegramClient
        self._clients: Dict[int, TelegramClient] = {}
        # Временные клиенты для авторизации (до сохранения в БД)
        self._pending: Dict[str, dict] = {}

    # ─── Internal helpers ────────────────────────────────────────────────────

    def _make_client(self, api_id: int, api_hash: str, session_string: Optional[str] = None) -> TelegramClient:
        session = StringSession(session_string) if session_string else StringSession()
        return TelegramClient(session, api_id, api_hash)

    def store_client(self, acc_id: int, client: TelegramClient):
        self._clients[acc_id] = client
        self.db.set_account_connected(acc_id, True)

    async def disconnect_account(self, acc_id: int):
        client = self._clients.pop(acc_id, None)
        if client and client.is_connected():
            await client.disconnect()
        self.db.set_account_connected(acc_id, False)

    async def reconnect_all(self):
        """При старте бота переподключаем все сохранённые аккаунты."""
        accounts = self.db.get_all_accounts()
        for acc in accounts:
            try:
                client = self._make_client(acc['api_id'], acc['api_hash'], acc['session_string'])
                await client.connect()
                if await client.is_user_authorized():
                    self._clients[acc['id']] = client
                    self.db.set_account_connected(acc['id'], True)
                    logger.info(f"Reconnected account #{acc['id']} ({acc['phone']})")
                else:
                    await client.disconnect()
                    self.db.set_account_connected(acc['id'], False)
                    logger.warning(f"Account #{acc['id']} not authorized")
            except Exception as e:
                logger.error(f"Failed to reconnect account #{acc['id']}: {e}")
                self.db.set_account_connected(acc['id'], False)

    def _get_client(self, acc_id: int) -> TelegramClient:
        client = self._clients.get(acc_id)
        if not client:
            raise RuntimeError(f"Аккаунт #{acc_id} не подключён. Переавторизуйтесь.")
        return client

    # ─── Auth flow ───────────────────────────────────────────────────────────

    async def send_code(self, api_id: int, api_hash: str, phone: str) -> str:
        """Отправляет код и возвращает phone_hash. Хранит временный клиент."""
        client = self._make_client(api_id, api_hash)
        await client.connect()
        result = await client.send_code_request(phone)
        self._pending[phone] = {
            "client": client,
            "api_id": api_id,
            "api_hash": api_hash,
            "phone_hash": result.phone_code_hash,
        }
        return result.phone_code_hash

    async def sign_in(self, api_id: int, api_hash: str, phone: str,
                      phone_hash: str, code: str):
        """Выполняет вход по коду. Возвращает ('2fa',) или (client, session_string)."""
        pending = self._pending.get(phone)
        client = pending["client"] if pending else self._make_client(api_id, api_hash)
        if not client.is_connected():
            await client.connect()
        try:
            await client.sign_in(phone, code, phone_code_hash=phone_hash)
            session_string = client.session.save()
            return client, session_string
        except SessionPasswordNeededError:
            self._pending[phone] = {**pending, "client": client}
            return "2fa"
        except (PhoneCodeExpiredError, PhoneCodeInvalidError) as e:
            await client.disconnect()
            self._pending.pop(phone, None)
            raise RuntimeError(f"Неверный или истёкший код: {e}")

    async def sign_in_2fa(self, api_id: int, api_hash: str, phone: str, password: str):
        """Вход с паролем 2FA."""
        pending = self._pending.get(phone)
        client = pending["client"] if pending else self._make_client(api_id, api_hash)
        if not client.is_connected():
            await client.connect()
        await client.sign_in(password=password)
        session_string = client.session.save()
        self._pending.pop(phone, None)
        return client, session_string

    # ─── Messaging ───────────────────────────────────────────────────────────

    async def send_message(self, acc_id: int, chat, text: str):
        client = self._get_client(acc_id)
        try:
            await client.send_message(chat, text)
        except FloodWaitError as e:
            logger.warning(f"FloodWait {e.seconds}s for acc #{acc_id}")
            await asyncio.sleep(e.seconds)
            await client.send_message(chat, text)

    async def delete_messages(self, acc_id: int, chat, count: int) -> int:
        client = self._get_client(acc_id)
        msgs = await client.get_messages(chat, limit=count)
        me = await client.get_me()
        my_msgs = [m for m in msgs if m.sender_id == me.id]
        if not my_msgs:
            return 0
        await client.delete_messages(chat, my_msgs)
        return len(my_msgs)

    # ─── Folders ─────────────────────────────────────────────────────────────

    async def get_folders(self, acc_id: int) -> List[Dict]:
        client = self._get_client(acc_id)
        result = await client(GetDialogFiltersRequest())
        folders = []
        for f in result.filters:
            if isinstance(f, DialogFilter):
                folders.append({"id": f.id, "title": f.title})
        return folders

    async def get_folder_chats(self, acc_id: int, folder_id: int) -> List[str]:
        client = self._get_client(acc_id)
        result = await client(GetDialogFiltersRequest())
        target_filter = None
        for f in result.filters:
            if isinstance(f, DialogFilter) and f.id == folder_id:
                target_filter = f
                break

        if not target_filter:
            return []

        chat_ids = []
        for peer in target_filter.include_peers:
            try:
                entity = await client.get_entity(peer)
                chat_ids.append(entity.id)
            except Exception as e:
                logger.warning(f"Could not resolve peer: {e}")

        return [str(c) for c in chat_ids]
