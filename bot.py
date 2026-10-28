#!/usr/bin/env python3
import base64
import html
import json
import logging
import math
import os
import re
import sys
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl, quote
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent


def load_env_file(path):
    """Читает KEY=VALUE из .env. Уже заданные переменные окружения не перезаписываются."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


load_env_file(ROOT / ".env")

CONFIG_PATH = ROOT / "config.json"
APP_ENV = os.environ.get("APP_ENV", "development").strip().lower()
ACCESS_PATH = Path(os.environ.get("ACCESS_PATH") or ROOT / "access.json")
AUDIT_PATH = Path(os.environ.get("AUDIT_PATH") or ROOT / "data" / "check_events.jsonl")
USER_STORE = None
DOC_RE = re.compile(r"^[0-9A-Za-zА-Яа-яЁё._/-]{1,50}$")
CACHE = {}
WAREHOUSE_CACHE = {"loaded_at": 0.0, "items": {}, "error": ""}
WAITING_FOR_DOC = set()
REGISTRATION = {}
AUDIT_LOCK = threading.Lock()
LOCAL_TIMEZONE = timezone(timedelta(hours=5), name="Asia/Tashkent")


MESSAGES = {
    "ru": {
        "result.header": "Заявка {number}",
        "result.cache": "♻️ Результат взят из кеша",
        "smartup.unavailable": "❌ Smartup недоступен: {error}",
        "smartup.route_unknown": "➡️ Невозможно определить, какую систему нужно проверять",
        "smartup.not_found": "❌ Smartup: заявка не найдена",
        "smartup.check_number": "➡️ Проверьте правильность номера заявки",
        "smartup.number_owner": "👤 Если номер верный — обратитесь к оператору",
        "smartup.found": "✅ Smartup: найдена",
        "result.status": "📌 Статус: {status}",
        "result.date": "📅 Дата: {date}",
        "result.warehouse": "🏢 Склад: {names}",
        "result.warehouse_code": "🏢 Код склада Smartup: {codes}",
        "status.cancelled": "🚫 Заявка отменена",
        "status.cancelled_checks": "➡️ Проверки WMS и бухгалтерии не требуются",
        "status.cancelled_owner": "👤 По вопросам отмены обратитесь к оператору",
        "status.draft": "⚠️ Заявка сохранена как черновик и ещё не запущена в работу",
        "status.draft_action": "➡️ Оператор должен завершить оформление заявки и перевести её в стадию «Новый»",
        "status.operator_owner": "👤 Обратитесь к оператору",
        "status.new": "⚠️ Заявка ещё не передана на финансовую проверку",
        "status.new_action": "➡️ Оператор должен перевести заявку в стадию «В обработке»",
        "status.new_next": "➡️ После этого заявка будет передана в финансовый отдел",
        "status.processing": "⏳ Заявка передана в финансовый отдел",
        "status.processing_check": "➡️ Выполняется проверка в системе «Плюсовой баланс»",
        "status.processing_wait": "👤 Если стадия долго не меняется — обратитесь в финансовый отдел",
        "routing.found": "ℹ️ Склад определён по данным WMS: {names}",
        "routing.scan": "ℹ️ Склад не сопоставлен с настройками: выполнен поиск по всем WMS",
        "wms.no_regions": "❌ WMS проверить не удалось: нет настроенных складов",
        "result.region": "📍 Регион: {name}",
        "status.unknown": "⚠️ Для этой стадии правило проверки ещё не настроено",
        "status.admin_owner": "👤 Обратитесь к администратору бота",
        "result.responsible": "👤 Ответственный: {owner}",
        "result.not_checked": "⚠️ Не проверено: {reason}",
        "result.action": "➡️ Что делать: {action}",
        "result.direct_action": "➡️ {action}",
        "outcome.cancelled": "ℹ️ ИТОГ: ЗАЯВКА ОТМЕНЕНА",
        "outcome.ok": "✅ ИТОГ: ОШИБОК НЕ НАЙДЕНО",
        "outcome.problem": "❌ ИТОГ: ЕСТЬ ПРОБЛЕМА",
        "outcome.incomplete": "⚠️ ИТОГ: ПРОВЕРКА НЕ ЗАВЕРШЕНА",
        "outcome.pending": "ℹ️ ИТОГ: ЗАЯВКА В РАБОТЕ",
        "result.checked_at": "🕒 Проверено: {value}",
        "result.duration": "⏱ {seconds:.1f} сек.",
        "report.pending": "⏳ В работе: {count}",
    },
    "uz": {
        "result.header": "Ariza {number}",
        "result.cache": "♻️ Natija keshdan olindi",
        "smartup.unavailable": "❌ Smartup ishlamayapti: {error}",
        "smartup.route_unknown": "➡️ Qaysi tizimni tekshirish kerakligini aniqlab bo'lmadi",
        "smartup.not_found": "❌ Smartup: ariza topilmadi",
        "smartup.check_number": "➡️ Ariza raqami to'g'riligini tekshiring",
        "smartup.number_owner": "👤 Raqam to'g'ri bo'lsa — operatorga murojaat qiling",
        "smartup.found": "✅ Smartup: topildi",
        "result.status": "📌 Holat: {status}",
        "result.date": "📅 Sana: {date}",
        "result.warehouse": "🏢 Ombor: {names}",
        "result.warehouse_code": "🏢 Smartup ombor kodi: {codes}",
        "status.cancelled": "🚫 Ariza bekor qilingan",
        "status.cancelled_checks": "➡️ WMS va buxgalteriyani tekshirish talab etilmaydi",
        "status.cancelled_owner": "👤 Bekor qilish bo'yicha operatorga murojaat qiling",
        "status.draft": "⚠️ Ariza qoralama sifatida saqlangan va hali ishga tushirilmagan",
        "status.draft_action": "➡️ Operator arizani rasmiylashtirishni tugatib, «Yangi» bosqichiga o'tkazishi kerak",
        "status.operator_owner": "👤 Operatorga murojaat qiling",
        "status.new": "⚠️ Ariza hali moliyaviy tekshiruvga yuborilmagan",
        "status.new_action": "➡️ Operator arizani «Qayta ishlanmoqda» bosqichiga o'tkazishi kerak",
        "status.new_next": "➡️ Shundan keyin ariza moliya bo'limiga yuboriladi",
        "status.processing": "⏳ Ariza moliya bo'limiga yuborildi",
        "status.processing_check": "➡️ «Plyusovoy balans» tizimida tekshiruv bajarilmoqda",
        "status.processing_wait": "👤 Bosqich uzoq vaqt o'zgarmasa, moliya bo'limiga murojaat qiling",
        "routing.found": "ℹ️ Ombor WMS ma'lumotlari bo'yicha aniqlandi: {names}",
        "routing.scan": "ℹ️ Ombor sozlamalarga mos kelmadi: barcha WMS tizimlarida qidirildi",
        "wms.no_regions": "❌ WMSni tekshirib bo'lmadi: sozlangan omborlar yo'q",
        "result.region": "📍 Hudud: {name}",
        "status.unknown": "⚠️ Bu bosqich uchun tekshiruv qoidasi hali sozlanmagan",
        "status.admin_owner": "👤 Bot administratoriga murojaat qiling",
        "result.responsible": "👤 Mas'ul: {owner}",
        "result.not_checked": "⚠️ Tekshirilmadi: {reason}",
        "result.action": "➡️ Nima qilish kerak: {action}",
        "result.direct_action": "➡️ {action}",
        "outcome.cancelled": "ℹ️ YAKUN: ARIZA BEKOR QILINGAN",
        "outcome.ok": "✅ YAKUN: XATOLAR TOPILMADI",
        "outcome.problem": "❌ YAKUN: MUAMMO BOR",
        "outcome.incomplete": "⚠️ YAKUN: TEKSHIRUV YAKUNLANMADI",
        "outcome.pending": "ℹ️ YAKUN: ARIZA JARAYONDA",
        "result.checked_at": "🕒 Tekshirildi: {value}",
        "result.duration": "⏱ {seconds:.1f} soniya",
        "report.pending": "⏳ Jarayonda: {count}",
    },
}


def message_text(language, key, **values):
    """Return a complete localized message; missing keys fail loudly in tests/startup."""
    language = normalized_language(language)
    try:
        template = MESSAGES[language][key]
    except KeyError as error:
        raise RuntimeError(f"Не найден перевод {key!r} для языка {language!r}") from error
    return template.format(**values)

BUTTON_CHECK = "🔍 Проверить заявку"
BUTTON_HELP = "ℹ️ Инструкция"
BUTTON_HEALTH = "🩺 Состояние систем"
BUTTON_CANCEL = "❌ Отмена"
BUTTON_REGISTER = "📝 Регистрация"
BUTTON_CHAT_ID = "🆔 ID чата"
BUTTON_LANG_RU = "🇷🇺 Русский"
BUTTON_LANG_UZ = "🇺🇿 O'zbekcha"
BUTTON_SEND_PHONE_RU = "📱 Отправить телефон"
BUTTON_SEND_PHONE_UZ = "📱 Telefonni yuborish"

BUTTONS = {
    "ru": {
        "check": BUTTON_CHECK,
        "help": BUTTON_HELP,
        "health": BUTTON_HEALTH,
        "cancel": BUTTON_CANCEL,
        "register": BUTTON_REGISTER,
        "chat_id": BUTTON_CHAT_ID,
    },
    "uz": {
        "check": "🔍 Arizani tekshirish",
        "help": "ℹ️ Qo'llanma",
        "health": "🩺 Tizimlar holati",
        "cancel": "❌ Bekor qilish",
        "register": "📝 Ro'yxatdan o'tish",
        "chat_id": "🆔 Chat ID",
    },
}


def normalized_language(value):
    return "uz" if str(value or "").strip().lower() == "uz" else "ru"


def button(language, name):
    return BUTTONS[normalized_language(language)][name]


def is_button(text, name):
    return any(text == values[name] for values in BUTTONS.values())


def main_keyboard(allowed=False, language="ru"):
    language = normalized_language(language)
    if not allowed:
        return {
            "keyboard": [
                [{"text": button(language, "register")}],
                [{"text": button(language, "help")}, {"text": button(language, "chat_id")}],
            ],
            "resize_keyboard": True,
            "is_persistent": True,
            "input_field_placeholder": "Ro'yxatdan o'ting" if language == "uz" else "Пройдите регистрацию",
        }
    return {
        "keyboard": [
            [{"text": button(language, "check")}],
            [{"text": button(language, "help")}, {"text": button(language, "health")}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
        "input_field_placeholder": "Ariza raqamini kiriting" if language == "uz" else "Введите номер заявки",
    }


def language_keyboard():
    return {
        "keyboard": [[{"text": BUTTON_LANG_RU}, {"text": BUTTON_LANG_UZ}]],
        "resize_keyboard": True,
        "one_time_keyboard": True,
        "input_field_placeholder": "Выберите язык / Tilni tanlang",
    }


def phone_keyboard(language):
    button_text = BUTTON_SEND_PHONE_UZ if language == "uz" else BUTTON_SEND_PHONE_RU
    return {
        "keyboard": [[{"text": button_text, "request_contact": True}], [{"text": button(language, "cancel")}]],
        "resize_keyboard": True,
        "one_time_keyboard": True,
        "input_field_placeholder": "+998 XX XXX XX XX",
    }

def force_reply_keyboard(language="ru"):
    return {
        "force_reply": True,
        "selective": True,
        "input_field_placeholder": (
            "Bir yoki bir nechta ariza IDsi" if normalized_language(language) == "uz"
            else "Один или несколько ID заявок"
        ),
    }


def help_text(language="ru"):
    if normalized_language(language) == "uz":
        return (
            "Botdan qanday foydalaniladi:\n\n"
            "1. «🔍 Arizani tekshirish» tugmasini bosing.\n"
            "2. Smartupdagi bir yoki bir nechta ariza IDsini yuboring.\n"
            "3. Bot joriy bosqich, ombor va aniqlangan muammolarni ko'rsatadi.\n\n"
            "Bir nechta raqamni bo'sh joy, vergul yoki yangi qatordan yuborish mumkin.\n"
            "/check 4411939 4411940 buyrug'idan ham foydalanish mumkin.\n\n"
            "Bosqichlar bo'yicha mas'ullar:\n"
            "• Qoralama — operator rasmiylashtirishni yakunlaydi.\n"
            "• Yangi — operator arizani qayta ishlashga o'tkazadi.\n"
            "• Qayta ishlanmoqda — moliya bo'limi tekshiradi.\n"
            "• Kutilmoqda — order, BYAS, yig'ish, savat va seriyalar tekshiriladi.\n"
            "• Jo'natildi yoki Yetkazildi — yig'ish, savat va seriyalar tekshiriladi.\n"
            "• Arxivlandi — hujjat o'tkazilishi, EHF holati va imzosi, zarur bo'lsa shartnoma tekshiriladi.\n\n"
            "Order yoki BYAS topilmasa, yig'ish tugallanmagan bo'lsa, savatda tovar qolsa "
            "yoki seriyalar mos kelmasa — omborga murojaat qiling.\n\n"
            "TMS tekshiruvi vaqtincha o'chirilgan."
        )
    return (
        "Как пользоваться ботом:\n\n"
        "1. Нажмите «🔍 Проверить заявку».\n"
        "2. Отправьте один или несколько ID заявок из Smartup.\n"
        "3. Бот покажет текущую стадию, склад и найденные проблемы.\n\n"
        "Несколько номеров можно отправить через пробел, запятую или каждый с новой строки.\n"
        "Можно сразу использовать команду /check 4411939 4411940.\n\n"
        "Ответственные по стадиям:\n"
        "• Черновик — оператор завершает оформление.\n"
        "• Новый — оператор переводит заявку в обработку.\n"
        "• В обработке — проверка финансовым отделом.\n"
        "• В ожидании — проверяются ордер, БЯС, сборка, корзина и серии.\n"
        "• Отгружен или Доставлен — проверяются сборка, корзина и серии.\n"
        "• Архивирован — проверяются проведение документа, статус и подпись ЭСФ, при необходимости договор.\n\n"
        "Если ордер или БЯС не найдены, сборка не завершена, в корзине остался товар "
        "или серии не совпали — обратитесь на склад.\n\n"
        "Проверка TMS временно отключена."
    )


# Result formatting is shared by both languages.  Keeping the translation here means
# business rules cannot silently diverge between the Russian and Uzbek interfaces.
UZ_REPLACEMENTS = {
    "Обратитесь на склад: товар необходимо убрать из корзины": "Omborga murojaat qiling: tovarni savatdan olib tashlash kerak",
    "Обратитесь на склад: проверьте серии товара внутри сборки": "Omborga murojaat qiling: yig'im ichidagi tovar seriyalarini tekshiring",
    "Заявка всё ещё находится в ожидании. Обратитесь на склад": "Ariza hali ham kutish bosqichida. Omborga murojaat qiling",
    "Обратитесь на склад для перевода заявки в статус «Доставлен»": "Arizani «Yetkazildi» holatiga o'tkazish uchun omborga murojaat qiling",
    "Заявка находится в черновике и ещё не запущена в работу": "Ariza qoralamada va hali ishga tushirilmagan",
    "Завершить оформление и перевести заявку в стадию «Новый»": "Rasmiylashtirishni tugatib, arizani «Yangi» bosqichiga o'tkazish",
    "Перевести заявку в стадию «В обработке»": "Arizani «Qayta ishlanmoqda» bosqichiga o'tkazish",
    "Склад должен перевести заявку дальше": "Ombor arizani keyingi bosqichga o'tkazishi kerak",
    "Для этой стадии правило проверки ещё не настроено": "Bu bosqich uchun tekshiruv qoidasi hali sozlanmagan",
    "Серии или количество товара в Smartup и WMS не совпадают": "Smartup va WMSdagi tovar seriyasi yoki miqdori mos emas",
    "Склад должен проверить товар внутри сборки": "Ombor yig'im ichidagi tovarni tekshirishi kerak",
    "Заявка сохранена как черновик и ещё не запущена в работу": "Ariza qoralama sifatida saqlangan va hali ishga tushirilmagan",
    "Оператор должен завершить оформление заявки и перевести её в стадию «Новый»": "Operator arizani rasmiylashtirishni tugatib, «Yangi» bosqichiga o'tkazishi kerak",
    "Заявка ещё не передана на финансовую проверку": "Ariza hali moliyaviy tekshiruvga yuborilmagan",
    "Оператор должен перевести заявку в стадию «В обработке»": "Operator arizani «Qayta ishlanmoqda» bosqichiga o'tkazishi kerak",
    "После этого заявка будет передана в финансовый отдел": "Shundan keyin ariza moliya bo'limiga yuboriladi",
    "Заявка передана в финансовый отдел": "Ariza moliya bo'limiga yuborildi",
    "Выполняется проверка в системе «Плюсовой баланс»": "«Plyusovoy balans» tizimida tekshiruv bajarilmoqda",
    "Если стадия долго не меняется — обратитесь в финансовый отдел": "Bosqich uzoq vaqt o'zgarmasa, moliya bo'limiga murojaat qiling",
    "Склад не сопоставлен с настройками: выполнен поиск по WMS": "Ombor sozlamalarga mos kelmadi: WMS bo'yicha qidiruv bajarildi",
    "WMS проверить не удалось: нет настроенных складов": "WMSni tekshirib bo'lmadi: sozlangan omborlar yo'q",
    "Для склада не настроена WMS": "Ombor uchun WMS sozlanmagan",
    "Расходный ордер не найден в WMS": "WMSda chiqim orderi topilmadi",
    "Сборка не начата: БЯС не выбрана": "Yig'ish boshlanmagan: BYAS tanlanmagan",
    "Сборка товара не завершена": "Tovarni yig'ish yakunlanmagan",
    "Сборка не завершена: несобранных позиций —": "Yig'ish yakunlanmagan, yig'ilmagan pozitsiyalar:",
    "В корзине осталось товара": "Savatda tovar qolgan",
    "Не удалось проверить серии товара": "Tovar seriyalarini tekshirib bo'lmadi",
    "Сверка серий не выполнена": "Seriyalar solishtirilmadi",
    "Сборка завершена, но заявка всё ещё находится в ожидании": "Yig'ish tugagan, ammo ariza hali ham kutish bosqichida",
    "Заявка ещё не переведена в статус «Доставлен»": "Ariza hali «Yetkazildi» holatiga o'tkazilmagan",
    "Доставленная заявка ещё не архивирована": "Yetkazilgan ariza hali arxivlanmagan",
    "Передать заявку в бухгалтерию": "Arizani buxgalteriyaga topshirish",
    "Склад должен убрать товар из корзины": "Ombor tovarni savatdan olib tashlashi kerak",
    "Склад должен собрать оставшиеся товары": "Ombor qolgan tovarlarni yig'ishi kerak",
    "Склад должен выбрать БЯС и начать сборку": "Ombor BYASni tanlab, yig'ishni boshlashi kerak",
    "Повторить проверку или обратиться на склад": "Tekshiruvni takrorlash yoki omborga murojaat qilish",
    "Обратиться к администратору интеграции": "Integratsiya administratoriga murojaat qilish",
    "Обратиться к администратору бота": "Bot administratoriga murojaat qilish",
    "Обратиться в бухгалтерию": "Buxgalteriyaga murojaat qilish",
    "Обратиться на склад": "Omborga murojaat qilish",
    "Обратитесь к оператору": "Operatorga murojaat qiling",
    "Обратитесь в бухгалтерию": "Buxgalteriyaga murojaat qiling",
    "Обратитесь на склад": "Omborga murojaat qiling",
    "Smartup недоступен": "Smartup ishlamayapti",
    "Не удалось получить данные из WMS": "WMSdan ma'lumot olib bo'lmadi",
    "Бухгалтерия недоступна, статус ЭСФ не проверен": "Buxgalteriya ishlamayapti, EHF holati tekshirilmadi",
    "Бухгалтерия недоступна": "Buxgalteriya ishlamayapti",
    "Невозможно определить, какую систему нужно проверять": "Qaysi tizimni tekshirish kerakligini aniqlab bo'lmaydi",
    "Проверьте правильность номера заявки": "Ariza raqami to'g'riligini tekshiring",
    "Если номер верный — обратитесь к оператору": "Raqam to'g'ri bo'lsa, operatorga murojaat qiling",
    "Проверить номер заявки; если он верный — обратиться к оператору": "Ariza raqamini tekshirish; to'g'ri bo'lsa operatorga murojaat qilish",
    "Заявка не найдена в Smartup": "Ariza Smartupda topilmadi",
    "Заявка отменена": "Ariza bekor qilingan",
    "Проверки WMS и бухгалтерии не требуются": "WMS va buxgalteriyani tekshirish talab etilmaydi",
    "По вопросам отмены обратитесь к оператору": "Bekor qilish bo'yicha operatorga murojaat qiling",
    "Документ не найден в бухгалтерии": "Hujjat buxgalteriyada topilmadi",
    "Документ в бухгалтерии не проведён": "Hujjat buxgalteriyada o'tkazilmagan",
    "Бухгалтерия: документ проведён": "Buxgalteriya: hujjat o'tkazilgan",
    "Бухгалтерия: документ не проведён": "Buxgalteriya: hujjat o'tkazilmagan",
    "Не удалось проверить статус ЭСФ": "EHF holatini tekshirib bo'lmadi",
    "Повторить проверку или обратиться в бухгалтерию": "Tekshiruvni takrorlash yoki buxgalteriyaga murojaat qilish",
    "Повторите проверку или обратитесь в бухгалтерию": "Tekshiruvni takrorlang yoki buxgalteriyaga murojaat qiling",
    "Обратитесь в бухгалтерию": "Buxgalteriyaga murojaat qiling",
    "ЭСФ не подписана": "EHF imzolanmagan",
    "Проверка договора не выполнена": "Shartnoma tekshiruvi bajarilmadi",
    "Договор в документе не заполнен": "Hujjatdagi shartnoma to'ldirilmagan",
    "Активный договор не найден": "Faol shartnoma topilmadi",
    "Проверка договора": "Shartnomani tekshirish",
    "Корреспондент заполнен": "Korrespondent to'ldirilgan",
    "Корреспондент не заполнен": "Korrespondent to'ldirilmagan",
    "Договор в документе": "Hujjatdagi shartnoma",
    "Активный договор": "Faol shartnoma",
    "Договоры совпадают": "Shartnomalar mos",
    "Договоры не совпадают": "Shartnomalar mos emas",
    "Договор: соответствует активному": "Shartnoma: faol shartnomaga mos",
    "Договор: не соответствует активному": "Shartnoma: faol shartnomaga mos emas",
    "Договор: проверить не удалось": "Shartnomani tekshirib bo'lmadi",
    "Не удалось проверить договор": "Shartnomani tekshirib bo'lmadi",
    "Для текущей стадии правило проверки не настроено": "Joriy bosqich uchun tekshiruv qoidasi sozlanmagan",
    "Расходный ордер не найден": "Chiqim orderi topilmadi",
    "расходный ордер найден": "chiqim orderi topildi",
    "Сборка товара не завершена": "Tovar yig'imi yakunlanmagan",
    "БЯС не выбрана": "BYAS tanlanmagan",
    "БЯС найдена": "BYAS topildi",
    "Сборка завершена": "Yig'ish yakunlandi",
    "Серии Smartup и WMS не совпадают": "Smartup va WMS seriyalari mos emas",
    "Серии Smartup и WMS совпадают": "Smartup va WMS seriyalari mos",
    "Несобранных позиций": "Yig'ilmagan pozitsiyalar",
    "Товаров с расхождениями": "Tafovutli tovarlar",
    "Позиций в корзине": "Savatdagi pozitsiyalar",
    "таблица ниже": "jadval quyida",
    "WMS не вернула список несобранных товаров": "WMS yig'ilmagan tovarlar ro'yxatini bermadi",
    "WMS не вернула состав корзины": "WMS savat tarkibini bermadi",
    "WMS не вернула состав сборки": "WMS yig'im tarkibini bermadi",
    "WMS не вернула логическое поле": "WMS mantiqiy maydonni bermadi",
    "WMS не вернула корректное числовое поле": "WMS to'g'ri raqamli maydonni bermadi",
    "WMS вернула неверный тип": "WMS noto'g'ri turdagi qiymatni berdi",
    "WMS вернула неверный формат": "WMS noto'g'ri formatni berdi",
    "WMS вернула некорректные строки": "WMS noto'g'ri qatorlarni berdi",
    "ответ WMS не является объектом": "WMS javobi obyekt emas",
    "ответ JSON не является объектом": "JSON javobi obyekt emas",
    "неверный или пустой URL": "URL noto'g'ri yoki bo'sh",
    "ошибка запроса": "so'rov xatosi",
    "Smartup не вернул товары заявки": "Smartup ariza tovarlarini bermadi",
    "Smartup вернул пустой список товаров": "Smartup bo'sh tovarlar ro'yxatini berdi",
    "В Smartup есть товар без product_code": "Smartupda product_codesiz tovar bor",
    "В Smartup не указана серия": "Smartupda seriya ko'rsatilmagan",
    "В Smartup не указано количество": "Smartupda miqdor ko'rsatilmagan",
    "В WMS есть строка сборки без product_code": "WMS yig'imida product_codesiz qator bor",
    "В WMS не указана серия": "WMSda seriya ko'rsatilmagan",
    "В WMS не указано количество": "WMSda miqdor ko'rsatilmagan",
    "WMS: расходный ордер найден": "WMS: chiqim orderi topildi",
    "WMS: расходный ордер не найден": "WMS: chiqim orderi topilmadi",
    "Не удалось проверить WMS": "WMSni tekshirib bo'lmadi",
    "Нет в сборке": "Yig'imda yo'q",
    "Лишняя серия в сборке": "Yig'imdagi ortiqcha seriya",
    "Количество не совпало": "Miqdor mos emas",
    "ожидалось": "kutilgan",
    "количество": "miqdor",
    "серия": "seriya",
    "Ещё расхождений": "Yana tafovutlar",
    "Документ проведён": "Hujjat o'tkazildi",
    "Документ не проведён": "Hujjat o'tkazilmadi",
    "документ найден": "hujjat topildi",
    "документ не найден": "hujjat topilmadi",
    "Ожидает подписи клиента": "Mijoz imzosini kutmoqda",
    "Подписан клиентом": "Mijoz tomonidan imzolangan",
    "Подписан": "Imzolangan",
    "Отклонён": "Rad etilgan",
    "Отклонен": "Rad etilgan",
    "ЭСФ": "EHF",
    "Заявка": "Ariza",
    "Smartup: найдена": "Smartup: topildi",
    "Smartup: заявка найдена": "Smartup: ariza topildi",
    "Smartup: заявка не найдена": "Smartup: ariza topilmadi",
    "Стадия заявки": "Ariza bosqichi",
    "Дата заявки": "Ariza sanasi",
    "Статус": "Holat",
    "Дата": "Sana",
    "Код склада Smartup": "Smartup ombor kodi",
    "Склад": "Ombor",
    "Регион": "Hudud",
    "Ответственный": "Mas'ul",
    "Причина": "Sabab",
    "Не проверено": "Tekshirilmadi",
    "Что делать": "Nima qilish kerak",
    "ИТОГ: ЗАЯВКА ОТМЕНЕНА": "YAKUN: ARIZA BEKOR QILINGAN",
    "ИТОГ: ОШИБОК НЕ НАЙДЕНО": "YAKUN: XATOLAR TOPILMADI",
    "ИТОГ: ЕСТЬ ПРОБЛЕМА": "YAKUN: MUAMMO BOR",
    "ИТОГ: ПРОВЕРКА НЕ ЗАВЕРШЕНА": "YAKUN: TEKSHIRUV YAKUNLANMADI",
    "Проверено": "Tekshirildi",
    "Время проверки": "Tekshiruv vaqti",
    "Результат взят из кеша": "Natija keshdan olindi",
    "Черновик": "Qoralama",
    "Отменён": "Bekor qilingan",
    "Архивирован": "Arxivlangan",
    "Новый": "Yangi",
    "В обработке": "Qayta ishlanmoqda",
    "В ожидании": "Kutilmoqda",
    "Отгружен": "Jo'natilgan",
    "Доставлен": "Yetkazilgan",
    "Статус не указан": "Holat ko'rsatilmagan",
    "Неизвестный статус": "Noma'lum holat",
    "Товар без наименования": "Nomsiz tovar",
    "не указано": "ko'rsatilmagan",
    "Состояние интеграций": "Integratsiyalar holati",
    "Недоступно систем": "Ishlamayotgan tizimlar",
    "Бухгалтерия": "Buxgalteriya",
    "TMS недоступна": "TMS ishlamayapti",
    "Водитель не назначен": "Haydovchi tayinlanmagan",
    "Водитель": "Haydovchi",
    "ТТН отправлена": "TTN yuborildi",
    "ТТН не отправлена": "TTN yuborilmadi",
    "Без названия": "Nomsiz",
    "внутренняя ошибка": "ichki xato",
    "сервер не ответил вовремя": "server o'z vaqtida javob bermadi",
    "нет соединения с сервером": "server bilan aloqa yo'q",
    "неверный логин или пароль": "login yoki parol noto'g'ri",
    "доступ запрещён": "kirish taqiqlangan",
    "неправильный URL или сервис не опубликован": "URL noto'g'ri yoki servis e'lon qilinmagan",
    "внутренняя ошибка HTTP-сервиса": "HTTP servisning ichki xatosi",
    "сервер вернул страницу вместо JSON": "server JSON o'rniga sahifa qaytardi",
    "Проверка завершилась ошибкой": "Tekshiruv xato bilan yakunlandi",
    "Не определён": "Aniqlanmagan",
    "Отчёт о работе бота за": "Bot ishlashi bo'yicha hisobot:",
    "Период с": "Boshlanish davri",
    "Всего проверок": "Jami tekshiruvlar",
    "Уникальных пользователей": "Noyob foydalanuvchilar",
    "Уникальных заявок": "Noyob arizalar",
    "Без ошибок": "Xatosiz",
    "С проблемами": "Muammoli",
    "Не завершены": "Yakunlanmagan",
    "Отменённые заявки": "Bekor qilingan arizalar",
    "Стадии заявок": "Ariza bosqichlari",
    "Самые активные пользователи": "Eng faol foydalanuvchilar",
    " дн.": " kun",
    "бухгалтерия": "buxgalteriya",
    "оператор": "operator",
    "склад": "ombor",
    "администратор интеграции": "integratsiya administratori",
    "администратор бота": "bot administratori",
    " ч.": " soat",
    " сек.": " soniya",
}


def localize_text(value, language):
    """Legacy translation for service details not yet emitted through message_text()."""
    text = str(value)
    if normalized_language(language) != "uz":
        return text
    for source in sorted(UZ_REPLACEMENTS, key=len, reverse=True):
        text = text.replace(source, UZ_REPLACEMENTS[source])
    return text


class UpdateDispatcher:
    """Run different users concurrently while preserving each user's message order."""

    def __init__(self, max_workers=4):
        self.executor = ThreadPoolExecutor(max_workers=max(1, int(max_workers)))
        self.lock = threading.Lock()
        self.queues = {}
        self.closed = False

    def submit(self, key, function, *args, **kwargs):
        with self.lock:
            if self.closed:
                raise RuntimeError("Диспетчер обновлений уже остановлен")
            queue = self.queues.setdefault(key, deque())
            queue.append((function, args, kwargs))
            if len(queue) == 1:
                self.executor.submit(self._drain, key)

    def _drain(self, key):
        while True:
            with self.lock:
                queue = self.queues.get(key)
                if not queue:
                    return
                function, args, kwargs = queue[0]
            try:
                function(*args, **kwargs)
            except Exception:
                logging.exception("update_handler_error key=%s", key)
            with self.lock:
                queue = self.queues.get(key)
                if not queue:
                    return
                queue.popleft()
                if not queue:
                    self.queues.pop(key, None)
                    return

    def shutdown(self, wait=False):
        with self.lock:
            self.closed = True
        self.executor.shutdown(wait=wait, cancel_futures=True)


def update_dispatch_key(update):
    if "message" in update:
        message = update["message"]
        chat_id = message.get("chat", {}).get("id")
        user_id = message.get("from", {}).get("id", chat_id)
        return str(chat_id), str(user_id)
    if "callback_query" in update:
        callback = update["callback_query"]
        chat_id = callback.get("message", {}).get("chat", {}).get("id")
        user_id = callback.get("from", {}).get("id", chat_id)
        return str(chat_id), str(user_id)
    return "update", str(update.get("update_id"))


def dispatch_update(dispatcher, config, update):
    key = update_dispatch_key(update)
    if "message" in update:
        dispatcher.submit(key, handle_message, config, update["message"])
    elif "callback_query" in update:
        dispatcher.submit(key, handle_callback, config, update["callback_query"])


class JsonUserStore:
    """Хранилище пользователей для разработки: access.json."""

    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()

    def describe(self):
        return f"JSON-файл {self.path}"

    def _load(self):
        empty = {"version": 2, "users": {}}
        if not self.path.exists():
            return empty
        try:
            with self.path.open("r", encoding="utf-8") as stream:
                data = json.load(stream)
            if not isinstance(data, dict):
                raise ValueError("неверная структура")

            users = data.get("users", {})
            if not isinstance(users, dict):
                raise ValueError("неверная структура")

            return {
                "version": 2,
                "users": {str(key): value for key, value in users.items() if isinstance(value, dict)},
            }
        except Exception:
            return empty

    def get_user(self, user_id):
        with self.lock:
            return self._load()["users"].get(str(user_id))

    def save_user(self, user):
        with self.lock:
            state = self._load()
            record = dict(user)
            record["registered_at"] = record["registered_at"].isoformat(timespec="seconds")
            state["users"][str(user["telegram_user_id"])] = record

            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.path.with_suffix(".json.tmp")
            with temp_path.open("w", encoding="utf-8") as stream:
                json.dump(state, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            temp_path.replace(self.path)

    def save_check_event(self, event):
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event, ensure_ascii=False, default=str) + "\n"
        with AUDIT_LOCK:
            with AUDIT_PATH.open("a", encoding="utf-8") as stream:
                stream.write(line)

    def get_check_events(self, since):
        if not AUDIT_PATH.exists():
            return []
        events = []
        with AUDIT_LOCK:
            lines = AUDIT_PATH.read_text(encoding="utf-8").splitlines()
        for line in lines:
            try:
                event = json.loads(line)
                checked_at = datetime.fromisoformat(str(event.get("checked_at") or ""))
                if checked_at.tzinfo is None:
                    checked_at = checked_at.replace(tzinfo=timezone.utc)
                if checked_at >= since:
                    events.append(event)
            except (ValueError, TypeError, json.JSONDecodeError):
                continue
        return events


class PostgresUserStore:
    """Хранилище пользователей для production: PostgreSQL."""

    COLUMNS = (
        "telegram_user_id", "username", "telegram_first_name", "telegram_last_name",
        "full_name", "position", "phones", "language", "registered_at",
    )
    SCHEMA = """
        CREATE TABLE IF NOT EXISTS users (
            telegram_user_id    BIGINT PRIMARY KEY,
            username            TEXT NOT NULL DEFAULT '',
            telegram_first_name TEXT NOT NULL DEFAULT '',
            telegram_last_name  TEXT NOT NULL DEFAULT '',
            full_name           TEXT NOT NULL,
            position            TEXT NOT NULL,
            phones              TEXT[] NOT NULL,
            language            TEXT NOT NULL,
            registered_at       TIMESTAMP NOT NULL
        )
    """
    CHECK_SCHEMA = """
        CREATE TABLE IF NOT EXISTS check_events (
            id                  BIGSERIAL PRIMARY KEY,
            checked_at          TIMESTAMPTZ NOT NULL,
            telegram_user_id    BIGINT,
            chat_id             BIGINT,
            username            TEXT NOT NULL DEFAULT '',
            full_name           TEXT NOT NULL DEFAULT '',
            language            TEXT NOT NULL DEFAULT 'ru',
            document_number     TEXT NOT NULL,
            smartup_status_code TEXT NOT NULL DEFAULT '',
            smartup_status      TEXT NOT NULL DEFAULT '',
            outcome             TEXT NOT NULL,
            elapsed_seconds     DOUBLE PRECISION NOT NULL DEFAULT 0,
            from_cache          BOOLEAN NOT NULL DEFAULT FALSE,
            warehouses          TEXT[] NOT NULL DEFAULT '{}',
            problem_reasons     TEXT[] NOT NULL DEFAULT '{}'
        )
    """

    def __init__(self):
        import psycopg
        from psycopg.rows import dict_row

        self.psycopg = psycopg
        self.row_factory = dict_row
        self.conninfo = os.environ.get("DATABASE_URL", "")
        self.params = {} if self.conninfo else {
            "host": os.environ.get("POSTGRES_HOST", "localhost"),
            "port": os.environ.get("POSTGRES_PORT", "5432"),
            "dbname": os.environ.get("POSTGRES_DB", "docstatus"),
            "user": os.environ.get("POSTGRES_USER", "docstatus"),
            "password": os.environ.get("POSTGRES_PASSWORD", ""),
        }
        self.conn = None
        self._execute(self.SCHEMA)
        self._execute(self.CHECK_SCHEMA)
        self._execute("CREATE INDEX IF NOT EXISTS check_events_checked_at_idx ON check_events (checked_at)")

    def describe(self):
        if self.conninfo:
            return "PostgreSQL (DATABASE_URL)"
        return f"PostgreSQL {self.params['host']}:{self.params['port']}/{self.params['dbname']}"

    def _execute(self, query, params=None):
        # Одно постоянное соединение; при обрыве переподключаемся один раз.
        for attempt in (1, 2):
            try:
                if self.conn is None or self.conn.closed:
                    self.conn = self.psycopg.connect(
                        self.conninfo, autocommit=True, row_factory=self.row_factory, **self.params
                    )
                with self.conn.cursor() as cursor:
                    cursor.execute(query, params)
                    return cursor.fetchone() if cursor.description else None
            except self.psycopg.OperationalError:
                if self.conn is not None:
                    self.conn.close()
                self.conn = None
                if attempt == 2:
                    raise

    def get_user(self, user_id):
        try:
            user_id = int(user_id)
        except (TypeError, ValueError):
            return None
        row = self._execute(
            f"SELECT {', '.join(self.COLUMNS)} FROM users WHERE telegram_user_id = %s", (user_id,)
        )
        if row is None:
            return None
        row["registered_at"] = row["registered_at"].isoformat(timespec="seconds")
        return row

    def save_user(self, user):
        columns = ", ".join(self.COLUMNS)
        placeholders = ", ".join(f"%({name})s" for name in self.COLUMNS)
        updates = ", ".join(f"{name} = EXCLUDED.{name}" for name in self.COLUMNS[1:])
        self._execute(
            f"INSERT INTO users ({columns}) VALUES ({placeholders}) "
            f"ON CONFLICT (telegram_user_id) DO UPDATE SET {updates}",
            user,
        )

    def save_check_event(self, event):
        columns = (
            "checked_at", "telegram_user_id", "chat_id", "username", "full_name", "language",
            "document_number", "smartup_status_code", "smartup_status", "outcome",
            "elapsed_seconds", "from_cache", "warehouses", "problem_reasons",
        )
        self._execute(
            f"INSERT INTO check_events ({', '.join(columns)}) VALUES "
            f"({', '.join(f'%({name})s' for name in columns)})",
            event,
        )

    def get_check_events(self, since):
        for attempt in (1, 2):
            try:
                if self.conn is None or self.conn.closed:
                    self.conn = self.psycopg.connect(
                        self.conninfo, autocommit=True, row_factory=self.row_factory, **self.params
                    )
                with self.conn.cursor() as cursor:
                    cursor.execute(
                        "SELECT checked_at, telegram_user_id, chat_id, username, full_name, language, "
                        "document_number, smartup_status_code, smartup_status, outcome, elapsed_seconds, "
                        "from_cache, warehouses, problem_reasons "
                        "FROM check_events WHERE checked_at >= %s ORDER BY checked_at",
                        (since,),
                    )
                    return [dict(row) for row in cursor.fetchall()]
            except self.psycopg.OperationalError:
                if self.conn is not None:
                    self.conn.close()
                self.conn = None
                if attempt == 2:
                    raise
        return []


def create_user_store():
    if APP_ENV == "production":
        return PostgresUserStore()
    if APP_ENV == "development":
        return JsonUserStore(ACCESS_PATH)
    raise RuntimeError(f"Неизвестное значение APP_ENV={APP_ENV!r}: ожидается development или production")


def get_registered_user(user_id):
    if user_id is None:
        return None
    return USER_STORE.get_user(user_id)


def get_user_language(user_id):
    user = get_registered_user(user_id)
    return normalized_language(user.get("language") if user else "ru")


def save_registered_user(message, registration):
    sender = message.get("from", {})
    user_id = sender.get("id")
    if user_id is None:
        raise ValueError("Telegram не передал user_id")

    USER_STORE.save_user({
        "telegram_user_id": user_id,
        "username": str(sender.get("username") or ""),
        "telegram_first_name": str(sender.get("first_name") or ""),
        "telegram_last_name": str(sender.get("last_name") or ""),
        "full_name": registration["full_name"],
        "position": registration["position"],
        "phones": registration["phones"],
        "language": registration["language"],
        "registered_at": datetime.now().replace(microsecond=0),
    })


def normalize_phone(value):
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) == 12 and digits.startswith("998"):
        return "+" + digits
    return ""


def parse_phone_list(value):
    parts = [part.strip() for part in re.split(r"[,;\n]+", str(value or "")) if part.strip()]
    if not parts:
        return [], []
    valid = []
    invalid = []
    for part in parts:
        phone = normalize_phone(part)
        if phone:
            if phone not in valid:
                valid.append(phone)
        else:
            invalid.append(part)
    return valid, invalid


def valid_full_name(value):
    normalized = " ".join(str(value or "").split())
    parts = normalized.split(" ") if normalized else []
    if len(parts) < 2 or len(normalized) > 120:
        return ""
    for part in parts:
        check = part.replace("-", "").replace("'", "").replace("’", "")
        if not check or not check.isalpha():
            return ""
    return normalized


def valid_position(value):
    normalized = " ".join(str(value or "").split())
    if len(normalized) < 2 or len(normalized) > 100:
        return ""
    return normalized


def start_registration(message, default_language="ru"):
    default_language = normalized_language(default_language)
    chat = message.get("chat", {})
    sender = message.get("from", {})
    chat_id = chat.get("id")
    user_id = sender.get("id")
    if chat_id is None or user_id is None:
        return (
            "⚠️ Foydalanuvchini aniqlab bo'lmadi." if default_language == "uz"
            else "⚠️ Не удалось определить пользователя."
        ), main_keyboard(False, default_language)
    if str(chat.get("type") or "") != "private":
        return (
            ("👤 Ro'yxatdan o'tish bot bilan shaxsiy chatda amalga oshiriladi.\n\n"
             "Botni shaxsiy xabarlarda ochib, /start yuboring."
             if default_language == "uz" else
             "👤 Регистрация выполняется в личном чате с ботом.\n\n"
             "Откройте бота в личных сообщениях и отправьте /start."),
            main_keyboard(False, default_language),
        )

    REGISTRATION[str(user_id)] = {
        "step": "language",
        "chat_id": str(chat_id),
        "language": "",
        "full_name": "",
        "position": "",
        "phones": [],
    }
    return "🇷🇺 Выберите язык\n🇺🇿 Tilni tanlang", language_keyboard()


def registration_active(user_id, chat_id):
    state = REGISTRATION.get(str(user_id))
    return bool(state and state.get("chat_id") == str(chat_id))


def process_registration_message(message):
    chat_id = message.get("chat", {}).get("id")
    user_id = message.get("from", {}).get("id")
    key = str(user_id)
    state = REGISTRATION.get(key)
    if not state or state.get("chat_id") != str(chat_id):
        return None, None

    text = str(message.get("text") or "").strip()
    step = state.get("step")
    language = normalized_language(state.get("language"))

    if is_button(text, "cancel") or text.lower() == "/cancel":
        REGISTRATION.pop(key, None)
        return (
            "Ro'yxatdan o'tish bekor qilindi." if language == "uz" else "Регистрация отменена."
        ), main_keyboard(False, language)

    if step == "language":
        if text == BUTTON_LANG_RU:
            state["language"] = "ru"
            state["step"] = "full_name"
            return "👤 Введите Ф.И.О", {"remove_keyboard": True}
        if text == BUTTON_LANG_UZ:
            state["language"] = "uz"
            state["step"] = "full_name"
            return "👤 F.I.Sh ni kiriting", {"remove_keyboard": True}
        return "Выберите язык кнопкой ниже. / Quyidagi tugma orqali tilni tanlang.", language_keyboard()

    if step == "full_name":
        full_name = valid_full_name(text)
        if not full_name:
            if language == "uz":
                return "❗ Noto'g'ri format. Ism va familiyani kiriting.\nMasalan: Ali Valiyev", {"remove_keyboard": True}
            return "❗ Неправильный формат. Введите минимум имя и фамилию.\nНапример: Иван Иванов", {"remove_keyboard": True}
        state["full_name"] = full_name
        state["step"] = "position"
        return ("💼 Lavozimingizni kiriting" if language == "uz" else "💼 Введите вашу должность"), {"remove_keyboard": True}

    if step == "position":
        position = valid_position(text)
        if not position:
            return (
                "❗ Lavozimni matn bilan kiriting." if language == "uz"
                else "❗ Введите должность текстом."
            ), {"remove_keyboard": True}
        state["position"] = position
        state["step"] = "phone"
        if language == "uz":
            answer = (
                "📱 Telefon raqamingizni yuboring yoki kiriting "
                "(bir nechta bo'lsa vergul bilan ajrating).\n"
                "Format: +998 XX XXX XX XX"
            )
        else:
            answer = (
                "📱 Отправьте или введите номер телефона "
                "(можно несколько через запятую).\n"
                "В таком формате: +998 XX XXX XX XX"
            )
        return answer, phone_keyboard(language)

    if step == "phone":
        contact = message.get("contact") or {}
        if contact:
            contact_user_id = contact.get("user_id")
            if contact_user_id is not None and str(contact_user_id) != str(user_id):
                return (
                    "❗ O'zingizning telefon raqamingizni yuboring." if language == "uz"
                    else "❗ Отправьте свой номер телефона."
                ), phone_keyboard(language)
            phone = normalize_phone(contact.get("phone_number"))
            phones = [phone] if phone else []
            invalid = [] if phone else [str(contact.get("phone_number") or "")]
        else:
            phones, invalid = parse_phone_list(text)

        if invalid or not phones:
            return (
                "❗ Noto'g'ri telefon formati. Masalan: +998901234567" if language == "uz"
                else "❗ Неправильный формат телефона. Например: +998901234567"
            ), phone_keyboard(language)

        state["phones"] = phones
        try:
            save_registered_user(message, state)
        except Exception as error:
            return (
                (f"⚠️ Ro'yxatdan o'tishni saqlab bo'lmadi: {error}" if language == "uz"
                 else f"⚠️ Не удалось сохранить регистрацию: {error}"),
                phone_keyboard(language),
            )

        REGISTRATION.pop(key, None)
        if language == "uz":
            answer = (
                "✅ Ro'yxatdan o'tish yakunlandi.\n\n"
                f"F.I.Sh: {state['full_name']}\n"
                f"Lavozim: {state['position']}\n"
                f"Telefon: {', '.join(phones)}\n\n"
                "Endi botdan foydalanishingiz mumkin."
            )
        else:
            answer = (
                "✅ Регистрация завершена.\n\n"
                f"Ф.И.О: {state['full_name']}\n"
                f"Должность: {state['position']}\n"
                f"Телефон: {', '.join(phones)}\n\n"
                "Теперь вы можете пользоваться ботом."
            )
        return answer, main_keyboard(True, language)

    REGISTRATION.pop(key, None)
    return (
        "Ro'yxatdan o'tish bekor qilindi. /start ni bosib, qayta urinib ko'ring."
        if language == "uz" else
        "Регистрация сброшена. Нажмите /start и попробуйте ещё раз."
    ), main_keyboard(False, language)

def load_config():
    if not CONFIG_PATH.exists():
        raise RuntimeError("Нет config.json. Скопируйте config.example.json в config.json и заполните его.")
    with CONFIG_PATH.open("r", encoding="utf-8") as stream:
        data = json.load(stream)
    if not str(data.get("telegram_bot_token", "")).strip():
        raise RuntimeError("В config.json не заполнен telegram_bot_token")
    return data


def add_doc_number(url, doc_number):
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["doc_number"] = doc_number
    encoded_path = quote(parts.path, safe="/:@-._~!$&'()*+,;=%")
    return urlunsplit((parts.scheme, parts.netloc, encoded_path, urlencode(query), parts.fragment))


def request_json(service, doc_number, timeout):
    url = add_doc_number(str(service.get("url", "")).strip(), doc_number)
    if not url.startswith(("http://", "https://")):
        return {"_error": "неверный или пустой URL"}

    headers = {"Accept": "application/json", "User-Agent": "OrderControlBot/1.0"}
    username = str(service.get("username", ""))
    password = str(service.get("password", ""))
    if username or password:
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"

    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            body = response.read().decode(charset, errors="replace")
            if response.status != 200:
                return {"_error": f"HTTP {response.status}"}
    except HTTPError as error:
        try:
            error_body = error.read().decode("utf-8", errors="replace").strip()
        except Exception:
            error_body = ""
        if error_body:
            try:
                error_data = json.loads(error_body)
                service_error = error_data.get("error") if isinstance(error_data, dict) else ""
                if service_error:
                    return {"_error": f"HTTP {error.code}: {service_error}"}
            except json.JSONDecodeError:
                pass
        return {"_error": f"HTTP {error.code}", "_technical": error_body[:3000]}
    except URLError as error:
        return {"_error": f"нет соединения: {error.reason}"}
    except TimeoutError:
        return {"_error": "превышено время ожидания"}
    except Exception as error:
        return {"_error": f"ошибка запроса: {error}"}

    try:
        data = json.loads(body)
        if not isinstance(data, dict):
            return {"_error": "ответ JSON не является объектом"}
        if data.get("error"):
            return {"_error": str(data["error"])}
        return data
    except json.JSONDecodeError:
        return {"_error": "база вернула не JSON"}



def normalize_wms(data):
    """Validate the service contract; never interpret missing fields as False."""
    if not isinstance(data, dict):
        return {"_error": "ответ WMS не является объектом"}
    if data.get("_error"):
        return data
    if data.get("error"):
        return {"_error": str(data["error"])}
    result = dict(data)
    fields = (
        ("document_found", ("order_found", "document_found")),
        ("assembly_found", ("assembly_found",)),
        ("assembled", ("assembly_completed", "assembled")),
    )
    for target, aliases in fields:
        value = next((data[key] for key in aliases if key in data), None)
        if not isinstance(value, bool):
            return {"_error": f"WMS не вернула логическое поле {target}"}
        result[target] = value
    quantity = data.get("basket_quantity")
    if (type(quantity) not in (int, float)
            or (isinstance(quantity, float) and not math.isfinite(quantity))
            or quantity < 0):
        return {"_error": "WMS не вернула корректное числовое поле basket_quantity"}
    flag = data.get("basket_has_items", False)
    if not isinstance(flag, bool):
        return {"_error": "WMS вернула неверный тип basket_has_items"}
    result["basket_has_items"] = flag or quantity > 0
    raw_finished = data.get("assembly_finished", result["assembled"])
    result["assembly_finished"] = raw_finished if isinstance(raw_finished, bool) else result["assembled"]
    result["assembled"] = (
        result["document_found"] and result["assembly_found"]
        and result["assembly_finished"] and not result["basket_has_items"]
    )

    result["assembly_items_available"] = "assembly_items" in data
    result["series_data_error"] = ""
    assembly_items = data.get("assembly_items", [])
    if not isinstance(assembly_items, list):
        result["assembly_items_available"] = False
        result["series_data_error"] = "WMS вернула неверный формат assembly_items"
        assembly_items = []
    elif any(not isinstance(item, dict) for item in assembly_items):
        result["assembly_items_available"] = False
        result["series_data_error"] = "WMS вернула некорректные строки assembly_items"
        assembly_items = []
    result["assembly_items"] = assembly_items

    for field in ("unassembled_items", "basket_items"):
        items = data.get(field, [])
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            return {"_error": f"WMS вернула неверный формат {field}"}
        result[field] = items

    # Keep supported aliases consistent with the calculated result.
    result["order_found"] = result["document_found"]
    result["assembly_completed"] = result["assembled"]
    return result


def request_wms(service, doc_number, timeout):
    return normalize_wms(request_json(service, doc_number, timeout))


def decimal_value(value):
    if isinstance(value, bool) or value is None:
        raise InvalidOperation
    text = str(value).strip().replace(" ", "").replace(",", ".")
    return Decimal(text)


def series_key(product_code, series):
    return str(product_code or "").strip().casefold(), str(series or "").strip().casefold()


def display_quantity(value):
    number = Decimal(value)
    if number == number.to_integral_value():
        return str(int(number))
    return format(number.normalize(), "f")


def display_order_datetime(value):
    text = str(value or "").strip()
    if not text:
        return ""
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        return datetime.fromisoformat(candidate).strftime("%d.%m.%Y %H:%M")
    except ValueError:
        pass
    for pattern in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M"):
        try:
            return datetime.strptime(text, pattern).strftime("%d.%m.%Y %H:%M")
        except ValueError:
            continue
    return text


def item_label(item):
    name = str(item.get("product_name") or "").strip()
    code = str(item.get("product_code") or "").strip()
    if name and code:
        return f"{name} ({code})"
    return name or code or "Товар без наименования"


def format_wms_items(items, quantity_field, limit=10):
    lines = []
    for item in items[:limit]:
        product = item_label(item)
        series = str(item.get("series") or "").strip()
        try:
            quantity = display_quantity(decimal_value(item.get(quantity_field)))
        except (InvalidOperation, ValueError):
            quantity = str(item.get(quantity_field) or "не указано").strip()
        series_text = f", серия {series}" if series else ""
        lines.append(f"   • {product}{series_text} — {quantity}")
    extra = len(items) - limit
    if extra > 0:
        lines.append(f"   • Ещё товаров: {extra}")
    return lines


def compare_series(smartup, wms):
    if not wms.get("assembly_items_available"):
        return {
            "available": False,
            "matches": False,
            "issues": [wms.get("series_data_error") or "WMS не вернула состав сборки"],
            "items": [],
        }

    order = smartup.get("order", {})
    products = order.get("order_products", []) if isinstance(order, dict) else []
    if not isinstance(products, list):
        return {"available": False, "matches": False, "issues": ["Smartup не вернул товары заявки"], "items": []}
    if not products:
        return {"available": False, "matches": False, "issues": ["Smartup вернул пустой список товаров"], "items": []}

    expected = {}
    actual = {}
    labels = {}
    data_issues = []

    for product in products:
        if not isinstance(product, dict):
            continue
        code = str(product.get("product_code") or "").strip()
        series = str(product.get("card_code") or "").strip()
        name = str(product.get("product_name") or product.get("name") or "").strip()
        # WMS must contain only the quantity that is still active after returns.
        # Smartup normally exposes it as sold_quant.  If that field is absent,
        # calculate the net quantity from order_quant - return_quant.
        quantity = product.get("sold_quant")
        if quantity is None and product.get("order_quant") is not None and product.get("return_quant") is not None:
            try:
                quantity = decimal_value(product["order_quant"]) - decimal_value(product["return_quant"])
            except (InvalidOperation, ValueError):
                quantity = None
        if quantity is None:
            quantity = next(
                (product.get(field) for field in ("quantity", "order_quant") if product.get(field) is not None),
                None,
            )
        if not code:
            data_issues.append("В Smartup есть товар без product_code")
            continue
        if not series:
            data_issues.append(f"В Smartup не указана серия: {name or code}")
            continue
        try:
            number = decimal_value(quantity)
        except (InvalidOperation, ValueError):
            data_issues.append(f"В Smartup не указано количество: {name or code}, серия {series}")
            continue
        key = series_key(code, series)
        expected[key] = expected.get(key, Decimal(0)) + number
        labels[key] = (code, series, name)

    for item in wms.get("assembly_items", []):
        code = str(item.get("product_code") or "").strip()
        series = str(item.get("series") or "").strip()
        name = str(item.get("product_name") or "").strip()
        if not code:
            data_issues.append("В WMS есть строка сборки без product_code")
            continue
        if not series:
            data_issues.append(f"В WMS не указана серия: {name or code}")
            continue
        try:
            number = decimal_value(item.get("quantity"))
        except (InvalidOperation, ValueError):
            data_issues.append(f"В WMS не указано количество: {name or code}, серия {series}")
            continue
        key = series_key(code, series)
        actual[key] = actual.get(key, Decimal(0)) + number
        labels.setdefault(key, (code, series, name))

    issues = list(dict.fromkeys(data_issues))
    mismatch_items = []
    for key in sorted(set(expected) | set(actual)):
        code, series, name = labels[key]
        product = f"{name} ({code})" if name else code
        expected_quantity = expected.get(key, Decimal(0))
        actual_quantity = actual.get(key, Decimal(0))
        if key not in actual:
            problem = "Нет в сборке"
            issues.append(f"{problem}: {product}, серия {series}, ожидалось {display_quantity(expected_quantity)}")
        elif key not in expected:
            problem = "Лишняя серия в сборке"
            issues.append(f"{problem}: {product}, серия {series}, количество {display_quantity(actual_quantity)}")
        elif expected_quantity != actual_quantity:
            problem = "Количество не совпало"
            issues.append(
                f"{problem}: {product}, серия {series}; Smartup {display_quantity(expected_quantity)}, "
                f"WMS {display_quantity(actual_quantity)}"
            )
        else:
            continue
        mismatch_items.append({
            "product_name": name or "Товар без наименования",
            "product_code": code,
            "series": series,
            "smartup_quantity": display_quantity(expected_quantity) if key in expected else "—",
            "wms_quantity": display_quantity(actual_quantity) if key in actual else "—",
            "problem": problem,
        })

    return {"available": True, "matches": not issues, "issues": issues, "items": mismatch_items}


def format_wms(wms, status_code, responsible="", series_check=None):
    wms = normalize_wms(wms)
    if wms.get("_error"):
        return [f"❌ Не удалось проверить WMS: {describe_error(wms['_error'])}"], 0, 1
    lines = []
    action = ""
    problems = 0
    warehouse_contact = False
    if not wms["document_found"]:
        lines.append("❌ WMS: расходный ордер не найден")
        action = "Обратитесь на склад"
        problems = 1
        warehouse_contact = True
    else:
        lines.append("✅ WMS: расходный ордер найден")
        if not wms["assembly_found"]:
            lines.append("⚠️ Сборка не начата: БЯС не выбрана")
            action = "Обратитесь на склад"
            problems = 1
            warehouse_contact = True
        elif not wms["assembly_finished"]:
            lines.extend(["✅ БЯС найдена", "⚠️ Сборка товара не завершена"])
            unassembled_items = wms.get("unassembled_items", [])
            if unassembled_items:
                lines.append(f"📦 Несобранных позиций: {len(unassembled_items)} — таблица ниже")
            else:
                lines.append("⚠️ WMS не вернула список несобранных товаров")
            action = "Обратитесь на склад"
            problems = 1
            warehouse_contact = True
        else:
            lines.extend(["✅ БЯС найдена", "✅ Сборка завершена"])
            if wms["basket_has_items"]:
                action = "Обратитесь на склад: товар необходимо убрать из корзины"
                problems = 1
                warehouse_contact = True
            elif series_check and not series_check.get("available"):
                lines.append("⚠️ Сверка серий не выполнена")
                for issue in series_check.get("issues", [])[:3]:
                    lines.append(f"   • {issue}")
                action = "Обратитесь на склад"
                problems = 1
                warehouse_contact = True
            elif series_check and not series_check.get("matches"):
                lines.append("❌ Серии Smartup и WMS не совпадают")
                mismatch_items = series_check.get("items", [])
                if mismatch_items:
                    lines.append(f"📦 Товаров с расхождениями: {len(mismatch_items)} — таблица ниже")
                for issue in series_check.get("issues", [])[:8]:
                    lines.append(f"   • {issue}")
                extra = len(series_check.get("issues", [])) - 8
                if extra > 0:
                    lines.append(f"   • Ещё расхождений: {extra}")
                action = "Обратитесь на склад: проверьте серии товара внутри сборки"
                problems = 1
                warehouse_contact = True
            elif series_check and series_check.get("matches"):
                lines.append("✅ Серии Smartup и WMS совпадают")
            else:
                lines.append("⚠️ Сверка серий не выполнена")
                action = "Обратитесь на склад"
                problems = 1
                warehouse_contact = True

            if not action and status_code == "B#W":
                action = "Заявка всё ещё находится в ожидании. Обратитесь на склад"
                problems = 1
                warehouse_contact = True
            elif not action and status_code == "B#S":
                action = "Обратитесь на склад для перевода заявки в статус «Доставлен»"
                problems = 1
                warehouse_contact = True
            elif not action and status_code == "B#V":
                action = "Передайте заявку в бухгалтерию для архивирования"
                problems = 1
    quantity = wms["basket_quantity"]
    if quantity > 0:
        lines.append(f"❌ В корзине осталось товара: {quantity:,}".replace(",", " ").removesuffix(".0"))
        basket_items = wms.get("basket_items", [])
        if basket_items:
            lines.append(f"🧺 Позиций в корзине: {len(basket_items)} — таблица ниже")
        else:
            lines.append("⚠️ WMS не вернула состав корзины")
    return lines, problems, 0


def request_smartup(service, doc_number, timeout):
    base_url = str(service.get("url", "")).strip().rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        return {"_error": "неверный или пустой URL"}

    path = str(service.get("path", "b/trade/txs/tdeal/order$export")).strip().lstrip("/")
    url = f"{base_url}/{path}"
    payload = {
        "filial_codes": [{"filial_code": ""}], "filial_code": "", "external_id": "", "deal_id": str(doc_number),
        "begin_deal_date": "", "end_deal_date": "", "delivery_date": "", "begin_created_on": "",
        "end_created_on": "", "begin_modified_on": "", "end_modified_on": ""
    }
    headers = {"Accept": "application/json", "Content-Type": "application/json", "User-Agent": "OrderControlBot/1.0"}
    username = str(service.get("username", ""))
    password = str(service.get("password", ""))
    if username or password:
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"

    request = Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            body = response.read().decode(charset, errors="replace")
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        return {"_error": f"HTTP {error.code}: {body[:500]}"}
    except URLError as error:
        return {"_error": f"нет соединения: {error.reason}"}
    except TimeoutError:
        return {"_error": "превышено время ожидания"}
    except Exception as error:
        return {"_error": f"ошибка запроса: {error}"}

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return {"_error": "Smartup вернул не JSON"}

    if isinstance(data, dict) and data.get("error"):
        return {"_error": str(data.get("error"))}

    result = data.get("result", data) if isinstance(data, dict) else {}
    orders = result.get("order", []) if isinstance(result, dict) else []
    if not orders:
        return {"document_found": False, "status_code": "", "status": "Не найден"}

    order = orders[0]
    status_code = str(order.get("status") or "").strip().upper() if isinstance(order, dict) else ""
    return {
        "document_found": True,
        "status_code": status_code,
        "status": smartup_status_name(status_code),
        "order": order,
    }


def smartup_headers(service, with_json=False):
    headers = {
        "Accept": "application/json",
        "User-Agent": "OrderControlBot/1.0",
    }
    if with_json:
        headers["Content-Type"] = "application/json"

    username = str(service.get("username", ""))
    password = str(service.get("password", ""))
    if username or password:
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"

    return headers


def request_smartup_warehouses(service, timeout):
    cache_seconds = int(service.get("warehouse_cache_seconds", 3600))
    if WAREHOUSE_CACHE["items"] and time.time() - WAREHOUSE_CACHE["loaded_at"] < cache_seconds:
        return WAREHOUSE_CACHE["items"], ""

    base_url = str(service.get("url", "")).strip().rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        return {}, "неверный или пустой URL Smartup"

    path = str(service.get("warehouse_path", "b/anor/mkw/warehouse_list:table")).strip().lstrip("/")
    url = f"{base_url}/{path}"
    columns = [
        "warehouse_id",
        "code",
        "name",
        "warehouse_type_name",
        "responsible_person_name",
        "order_no",
        "state",
        "state_name",
    ]
    limit = 50
    offset = 0
    rows = []

    while True:
        payload = {
            "p": {
                "column": columns,
                "filter": ["state", "=", "A"],
                "limit": limit,
                "offset": offset,
                "sort": [],
            }
        }
        request = Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=smartup_headers(service, with_json=True),
            method="POST",
        )

        try:
            with urlopen(request, timeout=timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                body = response.read().decode(charset, errors="replace")
        except HTTPError as error:
            error_body = error.read().decode("utf-8", errors="replace")
            message = f"HTTP {error.code}: {error_body[:500]}"
            WAREHOUSE_CACHE["error"] = message
            return {}, message
        except URLError as error:
            message = f"нет соединения: {error.reason}"
            WAREHOUSE_CACHE["error"] = message
            return {}, message
        except TimeoutError:
            WAREHOUSE_CACHE["error"] = "превышено время ожидания"
            return {}, WAREHOUSE_CACHE["error"]
        except Exception as error:
            message = f"ошибка запроса: {error}"
            WAREHOUSE_CACHE["error"] = message
            return {}, message

        try:
            response_data = json.loads(body)
        except json.JSONDecodeError:
            WAREHOUSE_CACHE["error"] = "Smartup вернул не JSON"
            return {}, WAREHOUSE_CACHE["error"]

        page_rows = response_data.get("data", []) if isinstance(response_data, dict) else []
        if not isinstance(response_data, dict) or response_data.get("error") or not isinstance(response_data.get("data"), list):
            return {}, "Smartup вернул некорректный справочник складов"
        raw_count = response_data.get("count")
        try:
            total_count = int(raw_count) if raw_count is not None else None
        except (TypeError, ValueError):
            total_count = None
        rows.extend(page_rows)

        if not page_rows or (total_count is not None and total_count > 0 and len(rows) >= total_count) or len(page_rows) < limit:
            break

        offset += limit

    warehouses = {}
    for row in rows:
        if not isinstance(row, list) or len(row) < 3:
            continue

        code = str(row[1] or "").strip()
        if not code:
            continue

        warehouses[code] = {
            "id": str(row[0] or "").strip(),
            "code": code,
            "name": str(row[2] or "").strip(),
            "region": str(row[3] or "").strip() if len(row) > 3 else "",
            "responsible": str(row[4] or "").strip() if len(row) > 4 else "",
            "active": str(row[6] or "").strip().upper() == "A" if len(row) > 6 else True,
        }

    if not warehouses:
        WAREHOUSE_CACHE["error"] = "Smartup вернул пустой справочник складов"
        return {}, WAREHOUSE_CACHE["error"]

    WAREHOUSE_CACHE["loaded_at"] = time.time()
    WAREHOUSE_CACHE["items"] = warehouses
    WAREHOUSE_CACHE["error"] = ""
    return warehouses, ""


def smartup_status_name(status_code):
    statuses = {
        "D": "Черновик",
        "C": "Отменён",
        "A": "Архивирован",
        "B#N": "Новый",
        "B#E": "В обработке",
        "B#W": "В ожидании",
        "B#S": "Отгружен",
        "B#V": "Доставлен",
    }
    return statuses.get(status_code, f"Неизвестный статус ({status_code})" if status_code else "Статус не указан")


def smartup_warehouse_codes(smartup):
    order = smartup.get("order", {})
    products = order.get("order_products", []) if isinstance(order, dict) else []
    codes = []
    for product in products:
        if not isinstance(product, dict):
            continue
        code = str(product.get("warehouse_code") or "").strip()
        if code and code not in codes:
            codes.append(code)
    return codes


def configured_warehouse_codes(region):
    values = region.get("warehouse_codes", [])
    if isinstance(values, str):
        values = [values]
    return {str(value).strip() for value in values if str(value).strip()}


def telegram_call(token, method, params, timeout=40):
    url = f"https://api.telegram.org/bot{token}/{method}"
    body = urlencode(params).encode("utf-8")
    request = Request(url, data=body, method="POST")
    with urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    if not data.get("ok"):
        raise RuntimeError(data.get("description", "Ошибка Telegram API"))
    return data.get("result")


def table_cell(text, header=False, align="left"):
    cell = {
        "text": str(text if text not in (None, "") else "—"),
        "align": align,
        "valign": "middle",
    }
    if header:
        cell["is_header"] = True
    return cell


def table_columns(table):
    if table.get("columns"):
        return table["columns"]
    language = normalized_language(table.get("language"))
    return [
        {"field": "product_name", "title": "Nomi" if language == "uz" else "Наименование"},
        {"field": "product_code", "title": "Kod" if language == "uz" else "Код", "align": "center"},
        {"field": "series", "title": "Seriya" if language == "uz" else "Серия", "align": "center"},
        {"field": table["quantity_field"], "title": table["quantity_title"], "align": "right", "quantity": True},
    ]


def table_display_value(item, column):
    value = item.get(column["field"])
    if column.get("quantity"):
        try:
            return display_quantity(decimal_value(value))
        except (InvalidOperation, ValueError):
            pass
    return str(value if value not in (None, "") else "—").strip()


def build_items_rich_message(table):
    columns = table_columns(table)
    cells = [[table_cell(column["title"], True, column.get("align", "left")) for column in columns]]

    for item in table["items"]:
        cells.append([
            table_cell(table_display_value(item, column), align=column.get("align", "left"))
            for column in columns
        ])

    return {
        "blocks": [
            {"type": "heading", "text": table["title"], "size": 4},
            {
                "type": "table",
                "cells": cells,
                "is_bordered": True,
                "is_striped": True,
                "is_compact": True,
            },
        ],
        "skip_entity_detection": True,
    }


def collect_wms_tables(results, language="ru"):
    language = normalized_language(language)
    tables = []
    for region in results.get("regions", []):
        if not region.get("selected", True):
            continue
        wms = normalize_wms(region.get("wms", {}))
        if wms.get("_error") or not wms.get("document_found"):
            continue
        region_name = str(region.get("name") or "").strip()
        suffix = f" — {region_name}" if region_name else ""
        unassembled = wms.get("unassembled_items", [])
        basket = wms.get("basket_items", [])
        if unassembled:
            tables.append({
                "title": (f"📦 Yig'ilmagan{suffix}" if language == "uz" else f"📦 Не собрано{suffix}"),
                "items": unassembled,
                "quantity_field": "remaining_quantity",
                "quantity_title": "Yig'ilmagan" if language == "uz" else "Не собрано",
                "language": language,
            })
        if basket:
            tables.append({
                "title": (f"🧺 Savatdagi tovarlar{suffix}" if language == "uz" else f"🧺 Товары в корзине{suffix}"),
                "items": basket,
                "quantity_field": "basket_quantity",
                "quantity_title": "Savatda" if language == "uz" else "В корзине",
                "language": language,
            })
        series_check = region.get("series_check") or {}
        mismatch_items = series_check.get("items", [])
        if mismatch_items:
            tables.append({
                "title": (
                    f"❌ Smartup va WMS tafovutlari{suffix}" if language == "uz"
                    else f"❌ Расхождения Smartup и WMS{suffix}"
                ),
                "items": mismatch_items,
                "language": language,
                "columns": [
                    {"field": "product_name", "title": "Nomi" if language == "uz" else "Наименование"},
                    {"field": "product_code", "title": "Kod" if language == "uz" else "Код", "align": "center"},
                    {"field": "series", "title": "Seriya" if language == "uz" else "Серия", "align": "center"},
                    {"field": "smartup_quantity", "title": "Smartup", "align": "right"},
                    {"field": "wms_quantity", "title": "WMS", "align": "right"},
                    {"field": "problem", "title": "Muammo" if language == "uz" else "Проблема"},
                ],
            })
    return tables


def send_items_table(token, chat_id, table, reply_parameters=None):
    if normalized_language(table.get("language")) == "uz":
        table = dict(table)
        table["items"] = [
            {key: (localize_text(value, "uz") if key == "problem" else value) for key, value in item.items()}
            for item in table["items"]
        ]
    rich_message = build_items_rich_message(table)
    params = {
        "chat_id": chat_id,
        "rich_message": json.dumps(rich_message, ensure_ascii=False),
    }
    if reply_parameters:
        params["reply_parameters"] = reply_parameters
    try:
        telegram_call(token, "sendRichMessage", params)
        return
    except Exception:
        lines = [table["title"] + ":"]
        columns = table_columns(table)
        for item in table["items"]:
            values = [f"{column['title']}: {table_display_value(item, column)}" for column in columns]
            lines.append("• " + "; ".join(values))
        for chunk in split_telegram_text("\n".join(lines)):
            fallback = {"chat_id": chat_id, "text": chunk}
            if reply_parameters:
                fallback["reply_parameters"] = reply_parameters
            telegram_call(token, "sendMessage", fallback)


def bool_value(data, *names):
    for name in names:
        if name in data:
            value = data[name]
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.strip().lower() in ("true", "1", "yes", "да")
            return bool(value)
    return False


def describe_error(error_text):
    text = str(error_text or "неизвестная ошибка")
    descriptions = {
        "HTTP 401": "неверный логин или пароль",
        "HTTP 403": "доступ запрещён",
        "HTTP 404": "неправильный URL или сервис не опубликован",
        "HTTP 500": "внутренняя ошибка HTTP-сервиса",
        "превышено время ожидания": "сервер не ответил вовремя",
        "база вернула не JSON": "сервер вернул страницу вместо JSON",
    }
    for prefix, description in descriptions.items():
        if prefix in text:
            return f"{prefix} — {description}"
    if text.startswith("нет соединения:"):
        return "нет соединения с сервером"
    return text[:180]


def responsible_line(name):
    name = str(name or "").strip()
    return f"   Ответственный: {name}" if name else ""


def format_region(region_name, wms, tms, wms_responsible="", tms_responsible="", series_check=None):
    lines = [f"📍 Регион: {region_name}"]
    problems = 0
    unavailable = 0

    wms_lines, wms_problems, wms_unavailable = format_wms(wms, "", wms_responsible, series_check)
    lines.extend(wms_lines)
    problems += wms_problems
    unavailable += wms_unavailable

    if tms.get("_error"):
        lines.append(f"❌ TMS недоступна: {describe_error(tms['_error'])}")
        unavailable += 1
    else:
        found = bool_value(tms, "order_found", "document_found")
        driver = str(tms.get("driver") or "").strip()
        ttn = bool_value(tms, "ttn_sent")
        lines.append("✅ TMS: документ найден" if found else "❌ TMS: документ не найден")
        if found:
            lines.append(f"✅ Водитель: {driver}" if driver else "⚠️ Водитель не назначен")
            lines.append("✅ ТТН отправлена" if ttn else "⚠️ ТТН не отправлена")
        if not found or not driver or not ttn:
            problems += 1
            owner = responsible_line(tms_responsible)
            if owner:
                lines.append(owner)

    return lines, problems, unavailable


def region_has_order(region_result):
    wms = region_result.get("wms", {})
    tms = region_result.get("tms", {})
    return (
        bool_value(wms, "order_found", "document_found")
        or bool_value(tms, "order_found", "document_found")
    )


def add_unique(items, value):
    value = str(value or "").strip()
    if value and value not in items:
        items.append(value)


def invoice_status_icon(invoice_status, invoice_signed):
    if invoice_signed:
        return "✅"
    status = str(invoice_status or "").strip().casefold()
    error_markers = (
        "ошиб", "отклон", "отмен", "аннулир", "отказ",
        "не принят", "не подписан",
    )
    return "❌" if any(marker in status for marker in error_markers) else "⚠️"


def format_accounting(accounting):
    """Format the accounting service response and keep its business rules in one place."""
    lines = []
    problem_reasons = []

    if accounting.get("_error"):
        return {
            "lines": [
                f"❌ Бухгалтерия недоступна: {describe_error(accounting['_error'])}",
                "➡️ Не удалось проверить статус ЭСФ",
            ],
            "problems": 0,
            "unavailable": 1,
            "problem_reasons": [],
            "unavailable_reasons": ["Бухгалтерия недоступна, статус ЭСФ не проверен"],
            "required_actions": ["Повторите проверку или обратитесь в бухгалтерию"],
            "responsible_people": [],
        }

    document_found = bool_value(accounting, "document_found", "order_found")
    document_posted = bool_value(accounting, "document_posted")
    invoice_signed = bool_value(accounting, "invoice_sent", "invoice_signed")
    invoice_status = str(accounting.get("invoice_status") or "").strip()
    accounting_completed = document_found and document_posted and invoice_signed

    if not document_found:
        lines.append("❌ Бухгалтерия: документ не найден")
    elif document_posted:
        lines.append("✅ Бухгалтерия: документ проведён")
    else:
        lines.append("❌ Бухгалтерия: документ не проведён")

    invoice_display = invoice_status or ("Подписан" if invoice_signed else "не найдена")
    invoice_icon = invoice_status_icon(invoice_status, invoice_signed)
    lines.append(f"{invoice_icon} ЭСФ: {invoice_display}")

    if accounting_completed:
        return {
            "lines": lines,
            "problems": 0,
            "unavailable": 0,
            "problem_reasons": [],
            "unavailable_reasons": [],
            "required_actions": [],
            "responsible_people": [],
        }

    if not document_found:
        add_unique(problem_reasons, "Документ не найден в бухгалтерии")
    elif not document_posted:
        add_unique(problem_reasons, "Документ в бухгалтерии не проведён")
    if not invoice_signed:
        invoice_reason = "ЭСФ не подписана"
        if invoice_status:
            invoice_reason += f" (статус: {invoice_status})"
        add_unique(problem_reasons, invoice_reason)

        # The current 1C contract checks the contract only while the invoice is unsigned.
        contract_check_required = bool_value(accounting, "contract_check_required")
        if contract_check_required:
            correspondent_filled = bool_value(accounting, "correspondent_filled")
            document_contract_filled = bool_value(accounting, "document_contract_filled")
            active_contract_found = bool_value(accounting, "active_contract_found")
            contract_matches = bool_value(accounting, "contract_matches")
            document_contract = str(accounting.get("document_contract") or "").strip()
            active_contract = str(accounting.get("active_contract") or "").strip()

            if not correspondent_filled:
                lines.append("❌ Корреспондент не заполнен")
                add_unique(problem_reasons, "Корреспондент не заполнен")
            if not document_contract_filled:
                add_unique(problem_reasons, "Договор в документе не заполнен")
            if not active_contract_found:
                add_unique(problem_reasons, "Активный договор не найден")
            contract_ok = (
                correspondent_filled and document_contract_filled
                and active_contract_found and contract_matches
            )
            if contract_ok:
                lines.append("✅ Договор: соответствует активному")
            else:
                if not document_contract_filled:
                    lines.append("❌ Договор в документе не заполнен")
                elif not active_contract_found:
                    lines.append("❌ Активный договор не найден")
                elif not contract_matches:
                    lines.append("❌ Договор: не соответствует активному")
                lines.append(f"📄 Договор в документе: {document_contract or '—'}")
                lines.append(f"📋 Активный договор: {active_contract or '—'}")
            if document_contract_filled and active_contract_found and not contract_matches:
                add_unique(problem_reasons, "Договоры не совпадают")
        else:
            lines.append("⚠️ Договор: проверить не удалось")
            add_unique(problem_reasons, "Не удалось проверить договор")

    return {
        "lines": lines,
        "problems": 1,
        "unavailable": 0,
        "problem_reasons": problem_reasons,
        "unavailable_reasons": [],
        "required_actions": ["Обратитесь в бухгалтерию"],
        "responsible_people": [],
    }


def wms_summary(wms, status_code, series_check=None, responsible=""):
    normalized = normalize_wms(wms)
    owner = str(responsible or "склад").strip()
    if normalized.get("_error"):
        return "Не удалось получить данные из WMS", "Повторить проверку или обратиться на склад", owner
    if not normalized["document_found"]:
        return "Расходный ордер не найден в WMS", "Обратиться на склад", owner
    if not normalized["assembly_found"]:
        return "Сборка не начата: БЯС не выбрана", "Склад должен выбрать БЯС и начать сборку", owner
    if not normalized["assembly_finished"]:
        count = len(normalized.get("unassembled_items", []))
        reason = f"Сборка не завершена: несобранных позиций — {count}" if count else "Сборка товара не завершена"
        return reason, "Склад должен собрать оставшиеся товары", owner
    if normalized["basket_has_items"]:
        quantity = normalized.get("basket_quantity", 0)
        return f"В корзине осталось товара: {quantity:g}", "Склад должен убрать товар из корзины", owner
    if series_check and not series_check.get("available"):
        return "Не удалось проверить серии товара", "Обратиться на склад", owner
    if series_check and not series_check.get("matches"):
        return "Серии или количество товара в Smartup и WMS не совпадают", "Склад должен проверить товар внутри сборки", owner
    if series_check is None:
        return "Сверка серий не выполнена", "Обратиться на склад", owner
    if status_code == "B#W":
        return "Сборка завершена, но заявка всё ещё находится в ожидании", "Склад должен перевести заявку дальше", owner
    if status_code == "B#S":
        return "Заявка ещё не переведена в статус «Доставлен»", "Обратиться на склад", owner
    if status_code == "B#V":
        return "Доставленная заявка ещё не архивирована", "Передать заявку в бухгалтерию", "бухгалтерия"
    return "", "", ""


def apply_wms_fallback_routing(results, configured_regions):
    """Keep only WMS regions that actually found the order during fallback search."""
    results["wms_matched_regions"] = []
    if results.get("warehouse_routing_exact", True):
        return

    matched_indexes = []
    for index, item in enumerate(results.get("regions", [])):
        if not item.get("selected", True):
            continue
        wms = item.get("wms", {})
        if (
            isinstance(wms, dict)
            and not wms.get("_error")
            and bool_value(wms, "document_found", "order_found")
        ):
            matched_indexes.append(index)

    if not matched_indexes:
        return

    matched_names = []
    warehouse_names = list(results.get("warehouse_names", []))
    for index, item in enumerate(results.get("regions", [])):
        selected = index in matched_indexes
        item["selected"] = selected
        if not selected:
            continue
        region = configured_regions[index] if index < len(configured_regions) else {}
        name = str(item.get("name") or region.get("name") or "").strip()
        if name and name not in matched_names:
            matched_names.append(name)
        warehouse_name = str(region.get("warehouse_name") or name).strip()
        if warehouse_name and warehouse_name not in warehouse_names:
            warehouse_names.append(warehouse_name)

    results["wms_matched_regions"] = matched_names
    results["warehouse_names"] = warehouse_names


def format_result(doc_number, results, language="ru"):
    language = normalized_language(language)
    lines = [message_text(language, "result.header", number=doc_number), ""]
    problems = 0
    unavailable = 0
    cancelled = False
    pending = False
    problem_reasons = []
    required_actions = []
    responsible_people = []
    unavailable_reasons = []

    smartup = results.get("smartup", {})
    if smartup.get("_error"):
        error = localize_text(describe_error(smartup["_error"]), language)
        lines.append(message_text(language, "smartup.unavailable", error=error))
        unavailable = 1
        lines.append(message_text(language, "smartup.route_unknown"))
        lines.append(message_text(
            language, "result.responsible",
            owner=localize_text("администратор интеграции", language),
        ))
        add_unique(unavailable_reasons, "Smartup недоступен")
        add_unique(required_actions, "Обратиться к администратору интеграции")
        add_unique(responsible_people, "администратор интеграции")
    elif not bool_value(smartup, "document_found"):
        lines.append(message_text(language, "smartup.not_found"))
        problems = 1
        lines.append(message_text(language, "smartup.check_number"))
        lines.append(message_text(language, "smartup.number_owner"))
        add_unique(problem_reasons, "Заявка не найдена в Smartup")
        add_unique(required_actions, "Проверить номер заявки; если он верный — обратиться к оператору")
        add_unique(responsible_people, "оператор")
    else:
        status_code = str(smartup.get("status_code") or "").strip().upper()
        status_name = str(smartup.get("status") or "Статус не указан")
        lines.append(message_text(language, "smartup.found"))
        lines.append(message_text(
            language, "result.status", status=localize_text(status_name, language),
        ))
        order_date = display_order_datetime(smartup.get("order", {}).get("deal_time"))
        if order_date:
            lines.append(message_text(language, "result.date", date=order_date))

        warehouse_codes = results.get("warehouse_codes", [])
        warehouse_names = results.get("warehouse_names", [])
        if warehouse_names:
            lines.append(message_text(language, "result.warehouse", names=", ".join(warehouse_names)))
        elif warehouse_codes:
            lines.append(message_text(language, "result.warehouse_code", codes=", ".join(warehouse_codes)))

        if status_code == "C":
            lines.append(message_text(language, "status.cancelled"))
            lines.append(message_text(language, "status.cancelled_checks"))
            lines.append(message_text(language, "status.cancelled_owner"))
            cancelled = True

        elif status_code == "D":
            lines.append(message_text(language, "status.draft"))
            lines.append(message_text(language, "status.draft_action"))
            lines.append(message_text(language, "status.operator_owner"))
            problems += 1
            add_unique(problem_reasons, "Заявка находится в черновике и ещё не запущена в работу")
            add_unique(required_actions, "Завершить оформление и перевести заявку в стадию «Новый»")
            add_unique(responsible_people, "оператор")

        elif status_code == "B#N":
            lines.append(message_text(language, "status.new"))
            lines.append(message_text(language, "status.new_action"))
            lines.append(message_text(language, "status.new_next"))
            lines.append(message_text(language, "status.operator_owner"))
            problems += 1
            add_unique(problem_reasons, "Заявка ещё не передана на финансовую проверку")
            add_unique(required_actions, "Перевести заявку в стадию «В обработке»")
            add_unique(responsible_people, "оператор")

        elif status_code == "B#E":
            lines.append(message_text(language, "status.processing"))
            lines.append(message_text(language, "status.processing_check"))
            lines.append(message_text(language, "status.processing_wait"))
            pending = True

        elif status_code in ("B#W", "B#S", "B#V"):
            region_results = [item for item in results.get("regions", []) if item.get("selected", True)]
            if not results.get("warehouse_routing_exact", True):
                matched_names = results.get("wms_matched_regions", [])
                if matched_names:
                    lines.append(message_text(
                        language, "routing.found", names=", ".join(matched_names),
                    ))
                else:
                    lines.append(message_text(language, "routing.scan"))
            if not region_results:
                lines.append(message_text(language, "wms.no_regions"))
                unavailable += 1
                add_unique(unavailable_reasons, "Для склада не настроена WMS")
                add_unique(required_actions, "Обратиться к администратору интеграции")
                add_unique(responsible_people, "администратор интеграции")
            for item in region_results:
                lines.extend(["", message_text(language, "result.region", name=item["name"])])
                details, issue_count, error_count = format_wms(
                    item.get("wms", {}), status_code, item.get("wms_responsible", ""),
                    item.get("series_check"),
                )
                lines.extend(localize_text(line, language) for line in details)
                problems += issue_count
                unavailable += error_count
                reason, action, owner = wms_summary(
                    item.get("wms", {}), status_code, item.get("series_check"),
                    item.get("wms_responsible", ""),
                )
                if error_count:
                    add_unique(unavailable_reasons, reason)
                elif issue_count:
                    add_unique(problem_reasons, reason)
                if issue_count or error_count:
                    add_unique(required_actions, action)
                    add_unique(responsible_people, owner)

        elif status_code == "A":
            accounting_result = format_accounting(results.get("accounting", {}))
            lines.extend(localize_text(line, language) for line in accounting_result["lines"])
            problems += accounting_result["problems"]
            unavailable += accounting_result["unavailable"]
            for reason in accounting_result["problem_reasons"]:
                add_unique(problem_reasons, reason)
            for reason in accounting_result["unavailable_reasons"]:
                add_unique(unavailable_reasons, reason)
            for action in accounting_result["required_actions"]:
                add_unique(required_actions, action)
            for owner in accounting_result["responsible_people"]:
                add_unique(responsible_people, owner)

        else:
            lines.append(message_text(language, "status.unknown"))
            lines.append(message_text(language, "status.admin_owner"))
            problems += 1
            add_unique(problem_reasons, "Для текущей стадии правило проверки не настроено")
            add_unique(required_actions, "Обратиться к администратору бота")
            add_unique(responsible_people, "администратор бота")

    elapsed = float(results.get("_elapsed", 0))
    lines.append("")
    if cancelled and unavailable == 0:
        lines.append(message_text(language, "outcome.cancelled"))
    elif pending and problems == 0 and unavailable == 0:
        lines.append(message_text(language, "outcome.pending"))
    elif problems == 0 and unavailable == 0:
        lines.append(message_text(language, "outcome.ok"))
    else:
        if problems > 0:
            lines.append(message_text(language, "outcome.problem"))
        else:
            lines.append(message_text(language, "outcome.incomplete"))
        for reason in unavailable_reasons:
            lines.append(message_text(
                language, "result.not_checked", reason=localize_text(reason, language),
            ))
        for action in required_actions:
            localized_action = localize_text(action, language)
            if action.startswith(("Обратитесь", "Повторите")):
                lines.append(message_text(language, "result.direct_action", action=localized_action))
            else:
                lines.append(message_text(language, "result.action", action=localized_action))
        for owner in responsible_people:
            lines.append(message_text(
                language, "result.responsible", owner=localize_text(owner, language),
            ))
    results["_summary"] = {
        "outcome": (
            "cancelled" if cancelled and unavailable == 0 else
            "pending" if pending and problems == 0 and unavailable == 0 else
            "ok" if problems == 0 and unavailable == 0 else
            "problem" if problems > 0 else "incomplete"
        ),
        "problem_reasons": problem_reasons + unavailable_reasons,
        "smartup_status_code": str(smartup.get("status_code") or ""),
        "smartup_status": str(smartup.get("status") or ""),
        "warehouses": list(results.get("warehouse_names", [])),
        "elapsed_seconds": elapsed,
    }
    lines.append(message_text(
        language, "result.checked_at", value=datetime.now(LOCAL_TIMEZONE).strftime("%d.%m.%Y %H:%M"),
    ))
    lines.append(message_text(language, "result.duration", seconds=elapsed))
    return "\n".join(lines)


def check_order(config, doc_number, language="ru"):
    language = normalized_language(language)
    cache_seconds = int(config.get("cache_seconds", 30))
    cache_key = (doc_number, language)
    cached = CACHE.get(cache_key)
    if cached and time.time() - cached[0] < cache_seconds:
        cached_result = cached[1]
        result = {
            "text": cached_result["text"] + "\n" + message_text(language, "result.cache"),
            "tables": cached_result.get("tables", []),
            "summary": dict(cached_result.get("summary", {})),
            "from_cache": True,
        }
        return result

    started = time.perf_counter()
    timeout = int(config.get("request_timeout_seconds", 20))
    regions = config.get("regions", [])
    if not regions:
        raise RuntimeError("В config.json не заполнен список regions")

    smartup = request_smartup(config.get("smartup", {}), doc_number, timeout)
    results = {
        "smartup": smartup,
        "regions": [
            {
                "name": str(region.get("name", "Без названия")),
                "wms_responsible": region.get("wms_responsible", ""),
                "tms_responsible": region.get("tms_responsible", ""),
            }
            for region in regions
        ]
    }

    status_code = ""
    if not smartup.get("_error") and bool_value(smartup, "document_found"):
        status_code = str(smartup.get("status_code") or "").strip().upper()

    warehouse_codes = smartup_warehouse_codes(smartup)
    results["warehouse_codes"] = warehouse_codes

    warehouses = {}
    warehouse_catalog_error = ""
    if warehouse_codes:
        warehouses, warehouse_catalog_error = request_smartup_warehouses(
            config.get("smartup", {}), timeout
        )
    results["warehouse_catalog_error"] = warehouse_catalog_error

    matched_region_indexes = []
    warehouse_names = []
    warehouse_details = []

    for code in warehouse_codes:
        warehouse = warehouses.get(code)
        if warehouse is None:
            continue

        name = warehouse.get("name", "")
        if name and name not in warehouse_names:
            warehouse_names.append(name)
        warehouse_details.append(warehouse)

    dynamic_names_found = bool(warehouse_names)
    for index, region in enumerate(regions):
        if set(warehouse_codes) & configured_warehouse_codes(region):
            matched_region_indexes.append(index)
            if not dynamic_names_found:
                warehouse_name = str(region.get("warehouse_name") or region.get("name") or "").strip()
                if warehouse_name and warehouse_name not in warehouse_names:
                    warehouse_names.append(warehouse_name)

    results["warehouse_routing_exact"] = bool(matched_region_indexes)
    results["warehouse_names"] = warehouse_names
    results["warehouse_details"] = warehouse_details

    indexes_to_check = list(matched_region_indexes) if matched_region_indexes else list(range(len(regions)))
    for index, item in enumerate(results["regions"]):
        item["selected"] = index in indexes_to_check

    need_accounting = status_code == "A"
    need_wms = status_code in ("B#W", "B#S", "B#V")

    max_workers = min(20, max(1, int(need_accounting) + len(regions) * int(need_wms)))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        jobs = {}

        if need_accounting:
            jobs[pool.submit(request_json, config.get("accounting", {}), doc_number, timeout)] = ("common", "accounting", None)

        for index in indexes_to_check:
            region = regions[index]
            if need_wms:
                jobs[pool.submit(request_wms, region.get("wms", {}), doc_number, timeout)] = ("region", "wms", index)

        for job in as_completed(jobs):
            scope, service_name, index = jobs[job]
            try:
                value = job.result()
            except Exception as error:
                value = {"_error": f"внутренняя ошибка: {error}"}

            if scope == "common":
                results[service_name] = value
            else:
                results["regions"][index][service_name] = value

    if need_wms:
        apply_wms_fallback_routing(results, regions)

    if need_wms and not smartup.get("_error"):
        for item in results["regions"]:
            wms = item.get("wms")
            if not isinstance(wms, dict) or wms.get("_error"):
                continue
            if (
                wms.get("document_found")
                and wms.get("assembly_found")
                and wms.get("assembly_finished")
                and not wms.get("basket_has_items")
            ):
                item["series_check"] = compare_series(smartup, wms)

    results["_elapsed"] = time.perf_counter() - started
    answer = format_result(doc_number, results, language)
    result = {
        "text": answer,
        "tables": collect_wms_tables(results, language),
        "summary": results.get("_summary", {}),
        "from_cache": False,
    }
    CACHE[cache_key] = (time.time(), result)
    return result


def health_status(name, result):
    if result.get("_error"):
        return f"❌ {name}: {describe_error(result['_error'])}", 1
    return f"✅ {name}", 0


def check_health(config):
    started = time.perf_counter()
    timeout = int(config.get("request_timeout_seconds", 20))
    health_doc = str(config.get("health_doc_number", "0"))
    regions = config.get("regions", [])
    tasks = []

    with ThreadPoolExecutor(max_workers=min(20, 2 + len(regions))) as pool:
        tasks.append(("Smartup", pool.submit(request_smartup, config.get("smartup", {}), health_doc, timeout)))
        tasks.append(("Бухгалтерия", pool.submit(request_json, config.get("accounting", {}), health_doc, timeout)))
        for region in regions:
            name = str(region.get("name", "Без названия"))
            tasks.append((f"WMS {name}", pool.submit(request_wms, region.get("wms", {}), health_doc, timeout)))

        lines = ["Состояние интеграций", ""]
        failed = 0
        for name, job in tasks:
            try:
                result = job.result()
            except Exception as error:
                result = {"_error": f"внутренняя ошибка: {error}"}
            line, error_count = health_status(name, result)
            lines.append(line)
            failed += error_count

    lines.extend(["", f"Недоступно систем: {failed}", f"Время проверки: {time.perf_counter() - started:.1f} сек."])
    return "\n".join(lines)


def is_allowed(config, chat_id, user_id=None):
    return get_registered_user(user_id) is not None

def handle_callback(config, callback):
    callback_id = callback.get("id")
    if not callback_id:
        return
    language = get_user_language(callback.get("from", {}).get("id"))
    try:
        telegram_call(
            config["telegram_bot_token"],
            "answerCallbackQuery",
            {
                "callback_query_id": callback_id,
                "text": (
                    "Qo'lda tasdiqlash endi ishlatilmaydi. /start orqali ro'yxatdan o'ting."
                    if language == "uz" else
                    "Ручное подтверждение больше не используется. Пройдите регистрацию через /start."
                ),
                "show_alert": "true",
            },
        )
    except Exception:
        pass

def parse_document_numbers(text):
    value = str(text or "").strip()
    if value.startswith("/"):
        parts = value.split(maxsplit=1)
        command = parts[0].split("@", 1)[0].lower()
        if command == "/check":
            value = parts[1] if len(parts) > 1 else ""

    documents = []
    invalid = []
    for item in re.split(r"[\s,;]+", value):
        number = item.strip()
        if not number:
            continue
        if not DOC_RE.fullmatch(number):
            invalid.append(number)
        elif number not in documents:
            documents.append(number)
    return documents, invalid


def user_mention_html(message, language="ru"):
    sender = message.get("from", {})
    user_id = sender.get("id")
    username = str(sender.get("username") or "").strip()
    first_name = str(sender.get("first_name") or "").strip()
    last_name = str(sender.get("last_name") or "").strip()
    name = f"@{username}" if username else " ".join(part for part in (first_name, last_name) if part)
    name = name or ("Foydalanuvchi" if normalized_language(language) == "uz" else "Пользователь")
    if user_id is None:
        return html.escape(name)
    return f'<a href="tg://user?id={user_id}">{html.escape(name)}</a>'


def split_telegram_text(text, limit=4000):
    chunks = []
    current = ""
    for line in str(text).splitlines(keepends=True):
        if len(line) > limit:
            if current:
                chunks.append(current.rstrip())
                current = ""
            while len(line) > limit:
                chunks.append(line[:limit])
                line = line[limit:]
        if len(current) + len(line) > limit:
            chunks.append(current.rstrip())
            current = line
        else:
            current += line
    if current:
        chunks.append(current.rstrip())
    return chunks or [""]


def check_orders(config, document_numbers, language="ru"):
    results = [None] * len(document_numbers)
    configured_workers = int(config.get("batch_workers", 3))
    workers = min(len(document_numbers), max(1, configured_workers))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        jobs = {
            pool.submit(check_order, config, number, language): index
            for index, number in enumerate(document_numbers)
        }
        for job in as_completed(jobs):
            index = jobs[job]
            try:
                results[index] = job.result()
            except Exception as error:
                results[index] = {
                    "text": localize_text((
                        f"Заявка {document_numbers[index]}\n\n"
                        f"❌ Проверка завершилась ошибкой: {describe_error(error)}"
                    ), language),
                    "tables": [],
                    "summary": {
                        "outcome": "incomplete", "problem_reasons": [str(error)],
                        "smartup_status_code": "", "smartup_status": "", "warehouses": [],
                        "elapsed_seconds": 0,
                    },
                    "from_cache": False,
                }
    return results


def save_check_audit(message, document_number, result, language):
    sender = message.get("from", {})
    chat = message.get("chat", {})
    registered = get_registered_user(sender.get("id")) or {}
    summary = result.get("summary", {})
    event = {
        "checked_at": datetime.now(LOCAL_TIMEZONE).isoformat(timespec="seconds"),
        "telegram_user_id": sender.get("id"),
        "chat_id": chat.get("id"),
        "username": str(sender.get("username") or ""),
        "full_name": str(registered.get("full_name") or ""),
        "language": normalized_language(language),
        "document_number": str(document_number),
        "smartup_status_code": str(summary.get("smartup_status_code") or ""),
        "smartup_status": str(summary.get("smartup_status") or ""),
        "outcome": str(summary.get("outcome") or "incomplete"),
        "elapsed_seconds": float(summary.get("elapsed_seconds") or 0),
        "from_cache": bool(result.get("from_cache")),
        "warehouses": list(summary.get("warehouses") or []),
        "problem_reasons": list(summary.get("problem_reasons") or []),
    }
    try:
        USER_STORE.save_check_event(event)
    except Exception:
        logging.exception("audit_write_failed document=%s user_id=%s", document_number, sender.get("id"))
    logging.info("check_event %s", json.dumps(event, ensure_ascii=False, default=str))


def is_admin(config, chat_id, user_id):
    configured = config.get("admin_chat_ids", [])
    if not isinstance(configured, list):
        configured = [configured]
    allowed = {str(value) for value in configured}
    return str(user_id) in allowed or str(chat_id) in allowed


def build_report(days, language="ru"):
    language = normalized_language(language)
    since = datetime.now(LOCAL_TIMEZONE) - timedelta(days=days)
    events = USER_STORE.get_check_events(since)
    outcome_counts = {"ok": 0, "problem": 0, "incomplete": 0, "cancelled": 0, "pending": 0}
    users = set()
    documents = set()
    statuses = {}
    user_counts = {}
    for event in events:
        outcome = str(event.get("outcome") or "incomplete")
        outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
        user_key = str(event.get("telegram_user_id") or event.get("username") or "unknown")
        users.add(user_key)
        documents.add(str(event.get("document_number") or ""))
        user_name = str(event.get("full_name") or event.get("username") or user_key)
        user_counts[user_name] = user_counts.get(user_name, 0) + 1
        status = str(event.get("smartup_status") or "Не определён")
        statuses[status] = statuses.get(status, 0) + 1

    top_users = sorted(user_counts.items(), key=lambda item: (-item[1], item[0]))[:5]
    status_rows = sorted(statuses.items(), key=lambda item: (-item[1], item[0]))
    lines = [
        f"📊 Отчёт о работе бота за {days} дн.",
        f"Период с: {since.strftime('%d.%m.%Y %H:%M')}",
        "",
        f"Всего проверок: {len(events)}",
        f"Уникальных пользователей: {len(users)}",
        f"Уникальных заявок: {len(documents)}",
        f"✅ Без ошибок: {outcome_counts.get('ok', 0)}",
        f"❌ С проблемами: {outcome_counts.get('problem', 0)}",
        f"⚠️ Не завершены: {outcome_counts.get('incomplete', 0)}",
        message_text(language, "report.pending", count=outcome_counts.get("pending", 0)),
        f"ℹ️ Отменённые заявки: {outcome_counts.get('cancelled', 0)}",
    ]
    if status_rows:
        lines.extend(["", "Стадии заявок:"])
        lines.extend(f"• {name}: {count}" for name, count in status_rows)
    if top_users:
        lines.extend(["", "Самые активные пользователи:"])
        lines.extend(f"• {name}: {count}" for name, count in top_users)
    return localize_text("\n".join(lines), language)


def send_batch_check(config, message, document_numbers, language="ru"):
    language = normalized_language(language)
    token = config["telegram_bot_token"]
    chat_id = message.get("chat", {}).get("id")
    message_id = message.get("message_id")
    mention = user_mention_html(message, language)
    count = len(document_numbers)
    if language == "uz":
        request_word = "ta arizani"
    elif count == 1:
        request_word = "заявку"
    elif 2 <= count <= 4:
        request_word = "заявки"
    else:
        request_word = "заявок"
    reply_parameters = None
    if message_id is not None:
        reply_parameters = json.dumps({"message_id": message_id, "allow_sending_without_reply": True})

    progress_message = None
    if count > 1:
        progress_params = {
            "chat_id": chat_id,
            "text": (
                f"🔎 {mention}, {count} {request_word} tekshiryapman…" if language == "uz"
                else f"🔎 {mention}, проверяю {count} {request_word}…"
            ),
            "parse_mode": "HTML",
        }
        if reply_parameters:
            progress_params["reply_parameters"] = reply_parameters
        progress_message = telegram_call(token, "sendMessage", progress_params)
    else:
        telegram_call(token, "sendChatAction", {"chat_id": chat_id, "action": "typing"})

    results = check_orders(config, document_numbers, language)
    for index, result in enumerate(results, start=1):
        save_check_audit(message, document_numbers[index - 1], result, language)
        heading = (
            (f"Natija {index}/{count}\n\n" if language == "uz" else f"Результат {index}/{count}\n\n")
            if count > 1 else ""
        )
        chunks = split_telegram_text(heading + result["text"])
        for chunk in chunks:
            params = {"chat_id": chat_id, "text": chunk}
            if reply_parameters:
                params["reply_parameters"] = reply_parameters
            telegram_call(token, "sendMessage", params)
        for table in result.get("tables", []):
            send_items_table(token, chat_id, table, reply_parameters)

    progress_message_id = progress_message.get("message_id") if isinstance(progress_message, dict) else None
    if progress_message_id is not None:
        try:
            telegram_call(token, "editMessageText", {
                "chat_id": chat_id,
                "message_id": progress_message_id,
                "text": (
                    f"✅ {mention}, tekshirilgan arizalar: {count}" if language == "uz"
                    else f"✅ {mention}, проверено заявок: {count}"
                ),
                "parse_mode": "HTML",
            })
        except Exception:
            logging.exception("progress_message_edit_failed chat_id=%s message_id=%s", chat_id, progress_message_id)


def handle_message(config, message):
    chat = message.get("chat", {})
    chat_id = chat.get("id")
    user_id = message.get("from", {}).get("id", chat_id)
    state_key = (str(chat_id), str(user_id))
    text = str(message.get("text", "")).strip()
    if chat_id is None:
        return

    reply_markup = None
    reply_parameters = None
    command = text.split(maxsplit=1)[0].split("@", 1)[0].lower() if text.startswith("/") else ""
    allowed = is_allowed(config, chat_id, user_id)
    language = get_user_language(user_id) if allowed else "ru"

    if command == "/chatid" or is_button(text, "chat_id"):
        answer = (
            f"🆔 Ushbu chat IDsi: {chat_id}\n👤 Sizning User IDingiz: {user_id}" if language == "uz"
            else f"🆔 ID этого чата: {chat_id}\n👤 Ваш User ID: {user_id}"
        )

    elif command == "/start":
        WAITING_FOR_DOC.discard(state_key)
        if allowed:
            REGISTRATION.pop(str(user_id), None)
            user = get_registered_user(user_id)
            if user:
                name = str(user.get("full_name") or "").strip()
                greeting = f", {name}" if name else ""
            else:
                greeting = ""
            answer = (
                (f"👋 Arizalarni nazorat qilish boti tayyor{greeting}.\n\n"
                 "«🔍 Arizani tekshirish» tugmasini bosing yoki ariza raqamini yuboring."
                 if language == "uz" else
                 f"👋 Бот контроля заявок готов{greeting}.\n\n"
                 "Нажмите «🔍 Проверить заявку» или просто отправьте её номер.")
            )
            reply_markup = main_keyboard(True, language)
        else:
            answer, reply_markup = start_registration(message)

    elif command == "/register" or is_button(text, "register"):
        WAITING_FOR_DOC.discard(state_key)
        answer, reply_markup = start_registration(message, language)

    elif registration_active(user_id, chat_id):
        WAITING_FOR_DOC.discard(state_key)
        answer, reply_markup = process_registration_message(message)
        allowed = is_allowed(config, chat_id, user_id)
        if allowed:
            language = get_user_language(user_id)

    elif command == "/help" or is_button(text, "help"):
        WAITING_FOR_DOC.discard(state_key)
        answer = help_text(language)

    elif not allowed:
        if str(chat.get("type") or "") == "private":
            answer = (
                "👤 Botdan foydalanish uchun ro'yxatdan o'ting. «📝 Ro'yxatdan o'tish»ni bosing yoki /start yuboring."
                if language == "uz" else
                "👤 Для работы с ботом нужно пройти регистрацию. Нажмите «📝 Регистрация» или отправьте /start."
            )
        else:
            answer = (
                "👤 Siz hali ro'yxatdan o'tmagansiz. Botni shaxsiy chatda ochib, /start yuboring."
                if language == "uz" else
                "👤 Вы ещё не зарегистрированы. Откройте бота в личных сообщениях и отправьте /start."
            )
        reply_markup = main_keyboard(False, language)

    elif command == "/health" or is_button(text, "health"):
        WAITING_FOR_DOC.discard(state_key)
        telegram_call(config["telegram_bot_token"], "sendChatAction", {"chat_id": chat_id, "action": "typing"})
        answer = localize_text(check_health(config), language)

    elif command == "/report":
        WAITING_FOR_DOC.discard(state_key)
        if not is_admin(config, chat_id, user_id):
            answer = "⛔ Bu buyruq faqat administratorlar uchun." if language == "uz" else "⛔ Эта команда доступна только администраторам."
        else:
            argument = text.split(maxsplit=1)[1].strip() if len(text.split(maxsplit=1)) > 1 else "7"
            try:
                days = int(argument)
                if not 1 <= days <= 365:
                    raise ValueError
                answer = build_report(days, language)
            except ValueError:
                answer = "Format: /report 7 (1 dan 365 kungacha)" if language == "uz" else "Формат: /report 7 (от 1 до 365 дней)"

    elif is_button(text, "check"):
        WAITING_FOR_DOC.add(state_key)
        answer = (
            "Smartupdagi ariza IDsini kiriting.\nMasalan: 4411939" if language == "uz"
            else "Введите ID заявки из Smartup.\nНапример: 4411939"
        )
        reply_markup = force_reply_keyboard(language)
        if message.get("message_id") is not None:
            reply_parameters = {"message_id": message["message_id"]}

    elif command == "/cancel" or is_button(text, "cancel"):
        WAITING_FOR_DOC.discard(state_key)
        answer = "Tekshiruv bekor qilindi." if language == "uz" else "Проверка отменена."

    else:
        documents, invalid = parse_document_numbers(text)
        max_batch = max(1, int(config.get("max_batch_documents", 10)))
        if command and command != "/check":
            answer = (
                "Buyruq aniqlanmadi. «ℹ️ Qo'llanma»ni bosing." if language == "uz"
                else "Команда не распознана. Нажмите «ℹ️ Инструкция»."
            )
        elif command == "/check" and not documents and not invalid:
            WAITING_FOR_DOC.add(state_key)
            answer = (
                "Smartupdagi bir yoki bir nechta ariza IDsini kiriting." if language == "uz"
                else "Введите один или несколько ID заявок из Smartup."
            )
            reply_markup = force_reply_keyboard(language)
            if message.get("message_id") is not None:
                reply_parameters = {"message_id": message["message_id"]}
        elif invalid or not documents:
            if state_key in WAITING_FOR_DOC:
                if language == "uz":
                    invalid_text = f"\nNoto'g'ri qiymatlar: {', '.join(invalid[:5])}" if invalid else ""
                    answer = (
                        "Format noto'g'ri. IDlarni bo'sh joy, vergul yoki yangi qatordan yuboring."
                        f"\nMasalan: 4411939 4411940{invalid_text}"
                    )
                else:
                    invalid_text = f"\nНекорректные значения: {', '.join(invalid[:5])}" if invalid else ""
                    answer = (
                        "Неверный формат. Отправьте ID через пробел, запятую или с новой строки."
                        f"\nНапример: 4411939 4411940{invalid_text}"
                    )
                reply_markup = force_reply_keyboard(language)
                if message.get("message_id") is not None:
                    reply_parameters = {"message_id": message["message_id"]}
            else:
                answer = (
                    "Buyruq aniqlanmadi. «ℹ️ Qo'llanma»ni bosing." if language == "uz"
                    else "Команда не распознана. Нажмите «ℹ️ Инструкция»."
                )
        elif len(documents) > max_batch:
            answer = (
                f"⚠️ Bir martada ko'pi bilan {max_batch} ta arizani tekshirish mumkin. Qabul qilindi: {len(documents)}."
                if language == "uz" else
                f"⚠️ За один раз можно проверить не больше {max_batch} заявок. Получено: {len(documents)}."
            )
            reply_markup = force_reply_keyboard(language)
            if message.get("message_id") is not None:
                reply_parameters = {"message_id": message["message_id"]}
        else:
            WAITING_FOR_DOC.discard(state_key)
            send_batch_check(config, message, documents, language)
            return

    if reply_markup is None:
        reply_markup = main_keyboard(is_allowed(config, chat_id, user_id), language)

    params = {
        "chat_id": chat_id,
        "text": answer,
        "reply_markup": json.dumps(reply_markup, ensure_ascii=False),
    }
    if reply_parameters is not None:
        params["reply_parameters"] = json.dumps(reply_parameters)
    telegram_call(config["telegram_bot_token"], "sendMessage", params)

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stdout,
        force=True,
    )


def main():
    global USER_STORE
    setup_logging()
    try:
        config = load_config()
    except Exception as error:
        logging.error("configuration_error: %s", error)
        return 1

    try:
        USER_STORE = create_user_store()
    except Exception as error:
        logging.error("storage_error environment=%s error=%s", APP_ENV, error)
        return 1
    logging.info("bot_started environment=%s user_store=%s audit_store=%s", APP_ENV, USER_STORE.describe(), AUDIT_PATH if APP_ENV == "development" else "PostgreSQL/check_events")

    token = config["telegram_bot_token"]
    dispatcher = UpdateDispatcher(config.get("update_workers", 4))
    offset = 0
    while True:
        try:
            updates = telegram_call(token, "getUpdates", {"offset": offset, "timeout": 30}, timeout=40)
            for update in updates:
                offset = max(offset, int(update["update_id"]) + 1)
                dispatch_update(dispatcher, config, update)
        except KeyboardInterrupt:
            dispatcher.shutdown(wait=False)
            logging.info("bot_stopped")
            return 0
        except Exception:
            logging.exception("update_loop_error")
            time.sleep(3)


if __name__ == "__main__":
    raise SystemExit(main())
