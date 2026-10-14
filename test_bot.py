import tempfile
import unittest
from pathlib import Path

import bot


class BotTests(unittest.TestCase):
    def test_series_mismatch_contains_complete_table_row(self):
        smartup = {
            "order": {
                "order_products": [{
                    "product_code": "A1",
                    "product_name": "Test product",
                    "card_code": "S1",
                    "sold_quant": 5,
                }]
            }
        }
        wms = {
            "assembly_items_available": True,
            "assembly_items": [{
                "product_code": "A1",
                "product_name": "Test product",
                "series": "S1",
                "quantity": 3,
            }],
        }

        result = bot.compare_series(smartup, wms)

        self.assertFalse(result["matches"])
        self.assertEqual(result["items"][0]["smartup_quantity"], "5")
        self.assertEqual(result["items"][0]["wms_quantity"], "3")
        self.assertEqual(result["items"][0]["problem"], "Количество не совпало")

    def test_uzbek_keyboard_and_result_translation(self):
        keyboard = bot.main_keyboard(True, "uz")
        self.assertEqual(keyboard["keyboard"][0][0]["text"], "🔍 Arizani tekshirish")
        translated = bot.localize_text("Заявка 42\n❌ ИТОГ: ЕСТЬ ПРОБЛЕМА", "uz")
        self.assertIn("Ariza 42", translated)
        self.assertIn("YAKUN: MUAMMO BOR", translated)

    def test_json_audit_can_build_report(self):
        old_path = bot.AUDIT_PATH
        old_store = bot.USER_STORE
        try:
            with tempfile.TemporaryDirectory() as directory:
                bot.AUDIT_PATH = Path(directory) / "events.jsonl"
                store = bot.JsonUserStore(Path(directory) / "users.json")
                bot.USER_STORE = store
                store.save_check_event({
                    "checked_at": bot.datetime.now(bot.LOCAL_TIMEZONE).isoformat(),
                    "telegram_user_id": 1,
                    "document_number": "42",
                    "outcome": "problem",
                    "smartup_status": "Доставлен",
                    "full_name": "Test User",
                })
                report = bot.build_report(7)
                self.assertIn("Всего проверок: 1", report)
                self.assertIn("Доставлен: 1", report)
        finally:
            bot.AUDIT_PATH = old_path
            bot.USER_STORE = old_store

    def test_accounting_completed_requires_posted_document_and_signed_invoice(self):
        result = bot.format_accounting({
            "document_found": True,
            "document_posted": True,
            "invoice_status": "Подписан клиентом",
            "invoice_sent": True,
            "accounting_completed": True,
            "contract_check_required": False,
        })

        text = "\n".join(result["lines"])
        self.assertEqual(text, "✅ Бухгалтерия: документ проведён\n✅ ЭСФ: Подписан клиентом")
        self.assertEqual(result["problems"], 0)

    def test_unsigned_invoice_shows_successful_contract_check(self):
        result = bot.format_accounting({
            "document_found": True,
            "document_posted": True,
            "invoice_status": "Ожидает подписи клиента",
            "invoice_sent": False,
            "accounting_completed": False,
            "contract_check_required": True,
            "correspondent_filled": True,
            "document_contract_filled": True,
            "active_contract_found": True,
            "contract_matches": True,
            "document_contract": "Договор №123",
            "active_contract": "Договор №123",
        })

        text = "\n".join(result["lines"])
        self.assertIn("⚠️ ЭСФ: Ожидает подписи клиента", text)
        self.assertIn("✅ Договор: соответствует активному", text)
        self.assertNotIn("Договор в документе:", text)
        self.assertNotIn("Активный договор:", text)
        self.assertEqual(result["problems"], 1)

    def test_contract_mismatch_shows_both_contracts_and_accounting_action(self):
        result = bot.format_accounting({
            "document_found": True,
            "document_posted": True,
            "invoice_status": "Отклонен",
            "invoice_sent": False,
            "accounting_completed": False,
            "contract_check_required": True,
            "correspondent_filled": True,
            "document_contract_filled": True,
            "active_contract_found": True,
            "contract_matches": False,
            "document_contract": "Договор №123",
            "active_contract": "Договор №456",
        })

        text = "\n".join(result["lines"])
        self.assertIn("❌ Договор: не соответствует активному", text)
        self.assertIn("Договор в документе: Договор №123", text)
        self.assertIn("Активный договор: Договор №456", text)
        self.assertIn("Договоры не совпадают", result["problem_reasons"])
        self.assertEqual(result["required_actions"], ["Обратитесь в бухгалтерию"])

    def test_missing_accounting_document_is_a_problem(self):
        result = bot.format_accounting({
            "document_found": False,
            "document_posted": False,
            "invoice_status": "",
            "invoice_sent": False,
            "accounting_completed": False,
            "contract_check_required": True,
            "correspondent_filled": False,
            "document_contract_filled": False,
            "active_contract_found": False,
            "contract_matches": False,
            "document_contract": "",
            "active_contract": "",
        })

        text = "\n".join(result["lines"])
        self.assertIn("Бухгалтерия: документ не найден", text)
        self.assertIn("ЭСФ: не найдена", text)
        self.assertIn("Договор в документе: —", text)
        self.assertIn("Активный договор: —", text)
        self.assertIn("Документ не найден в бухгалтерии", result["problem_reasons"])

    def test_archived_order_uses_extended_accounting_result(self):
        results = {
            "smartup": {
                "document_found": True,
                "status_code": "A",
                "status": "Архивирован",
                "order": {},
            },
            "accounting": {
                "document_found": True,
                "document_posted": True,
                "invoice_status": "Отклонен",
                "invoice_sent": False,
                "accounting_completed": False,
                "contract_check_required": True,
                "correspondent_filled": True,
                "document_contract_filled": True,
                "active_contract_found": True,
                "contract_matches": False,
                "document_contract": "Договор №123",
                "active_contract": "Договор №456",
            },
            "warehouse_codes": [],
            "warehouse_names": [],
            "_elapsed": 0,
        }

        text = bot.format_result("4535451", results)

        self.assertIn("✅ Smartup: найдена", text)
        self.assertIn("📌 Статус: Архивирован", text)
        self.assertIn("❌ ЭСФ: Отклонен", text)
        self.assertIn("Договор: не соответствует активному", text)
        self.assertIn("➡️ Обратитесь в бухгалтерию", text)
        self.assertNotIn("🔎 Причина:", text)
        self.assertEqual(results["_summary"]["outcome"], "problem")

    def test_invoice_status_uses_error_icon_for_failed_statuses(self):
        for status in ("Ошибка", "Отклонен", "Отменён", "Аннулирован", "Отказ"):
            with self.subTest(status=status):
                self.assertEqual(bot.invoice_status_icon(status, False), "❌")
        self.assertEqual(bot.invoice_status_icon("Ожидает подписи клиента", False), "⚠️")
        self.assertEqual(bot.invoice_status_icon("Подписан", True), "✅")

    def test_successful_archived_order_is_compact(self):
        results = {
            "smartup": {
                "document_found": True,
                "status_code": "A",
                "status": "Архивирован",
                "order": {"deal_time": "2026-10-13T12:08:45"},
            },
            "accounting": {
                "document_found": True,
                "document_posted": True,
                "invoice_status": "Подписан",
                "invoice_sent": True,
                "accounting_completed": True,
                "contract_check_required": False,
            },
            "warehouse_codes": ["8"],
            "warehouse_names": ["Склад Наманган"],
            "_elapsed": 0.4,
        }

        text = bot.format_result("4544666", results)

        self.assertIn("✅ Smartup: найдена", text)
        self.assertIn("📌 Статус: Архивирован", text)
        self.assertIn("📅 Дата: 13.10.2026 12:08", text)
        self.assertIn("🏢 Склад: Склад Наманган", text)
        self.assertIn("✅ Бухгалтерия: документ проведён", text)
        self.assertIn("✅ ЭСФ: Подписан", text)
        self.assertNotIn("документ найден", text)
        self.assertNotIn("Бухгалтерия завершена", text)
        self.assertNotIn("Текущий статус ЭСФ", text)
        self.assertIn("⏱ 0.4 сек.", text)

    def test_batch_progress_is_edited_and_single_check_has_no_final_message(self):
        calls = []
        old_telegram_call = bot.telegram_call
        old_check_orders = bot.check_orders
        old_save_check_audit = bot.save_check_audit

        def fake_telegram_call(token, method, params, timeout=40):
            calls.append((method, dict(params)))
            if method == "sendMessage" and str(params.get("text", "")).startswith("🔎"):
                return {"message_id": 77}
            return {}

        def fake_check_orders(config, numbers, language="ru"):
            return [{"text": f"Заявка {number}", "tables": [], "summary": {}} for number in numbers]

        try:
            bot.telegram_call = fake_telegram_call
            bot.check_orders = fake_check_orders
            bot.save_check_audit = lambda *args: None
            message = {"message_id": 10, "chat": {"id": 20}, "from": {"id": 30, "username": "user"}}

            bot.send_batch_check({"telegram_bot_token": "token"}, message, ["1", "2"], "ru")
            edits = [params for method, params in calls if method == "editMessageText"]
            self.assertEqual(len(edits), 1)
            self.assertEqual(edits[0]["message_id"], 77)
            self.assertIn("проверено заявок: 2", edits[0]["text"])
            self.assertFalse(any("проверка завершена" in str(params.get("text", "")).lower() for _, params in calls))

            calls.clear()
            bot.send_batch_check({"telegram_bot_token": "token"}, message, ["1"], "ru")
            self.assertTrue(any(method == "sendChatAction" for method, _ in calls))
            self.assertFalse(any(method == "editMessageText" for method, _ in calls))
            self.assertEqual(sum(method == "sendMessage" for method, _ in calls), 1)
        finally:
            bot.telegram_call = old_telegram_call
            bot.check_orders = old_check_orders
            bot.save_check_audit = old_save_check_audit


if __name__ == "__main__":
    unittest.main()
