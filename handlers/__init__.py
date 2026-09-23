"""Централизованная регистрация всех обработчиков."""
from telegram.ext import Application

from handlers import admin, common, contacts, menu, schedule


def register_all(app: Application) -> None:
    """
    Порядок важен: ConversationHandler'ы админки регистрируются первыми,
    чтобы перехватывать текстовые сообщения внутри диалогов.
    """
    admin.register(app)
    common.register(app)
    schedule.register(app)
    menu.register(app)
    contacts.register(app)