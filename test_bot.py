import tempfile
import threading
import unittest
from pathlib import Path

import bot


class BotTests(unittest.TestCase):
    def test_message_catalog_has_same_keys_for_every_language(self):
        self.assertEqual(set(bot.MESSAGES["ru"]), set(bot.MESSAGES["uz"]))

    def test_message_and_callback_share_dispatch_queue(self):
        message_update = {
            "message": {"chat": {"id": 1}, "from": {"id": 2}},
        }
        callback_update = {
            "callback_query": {
                "message": {"chat": {"id": 1}},
                "from": {"id": 2},
            },
        }
        self.assertEqual(
            bot.update_dispatch_key(message_update),
            bot.update_dispatch_key(callback_update),
        )

    def test_update_dispatcher_serializes_one_user_but_not_other_users(self):
        dispatcher = bot.UpdateDispatcher(max_workers=2)
        release_first = threading.Event()
        other_finished = threading.Event()
        same_user_finished = threading.Event()
        calls = []

        def first():
            calls.append("first-start")
            release_first.wait(2)
            calls.append("first-end")

        def second():
            calls.append("second")
            same_user_finished.set()

        def other():
            calls.append("other")
            other_finished.set()

        try:
            dispatcher.submit(("message", "1", "10"), first)
            dispatcher.submit(("message", "1", "10"), second)
            dispatcher.submit(("message", "1", "20"), other)

            self.assertTrue(other_finished.wait(1))
            self.assertFalse(same_user_finished.is_set())
            release_first.set()
            self.assertTrue(same_user_finished.wait(1))
            self.assertLess(calls.index("first-end"), calls.index("second"))
        finally:
            release_first.set()
            dispatcher.shutdown(wait=True)

    def test_wms_fallback_keeps_only_regions_that_found_the_order(self):
        results = {
            "warehouse_routing_exact": False,
            "warehouse_names": [],
            "regions": [
                {"name": "Недоступный", "selected": True, "wms": {"_error": "timeout"}},
                {"name": "Наманган", "selected": True, "wms": {"document_found": True}},
                {"name": "Другой", "selected": True, "wms": {"document_found": False}},
            ],
        }
        configured = [
            {"name": "Недоступный"},
            {"name": "Наманган", "warehouse_name": "Склад Наманган"},
            {"name": "Другой"},
        ]

        bot.apply_wms_fallback_routing(results, configured)

        self.assertEqual([item["selected"] for item in results["regions"]], [False, True, False])
        self.assertEqual(results["wms_matched_regions"], ["Наманган"])
        self.assertEqual(results["warehouse_names"], ["Склад Наманган"])

    def test_unrelated_wms_error_is_not_shown_after_fallback_match(self):
        good_wms = {
            "document_found": True,
            "assembly_found": True,
            "assembled": True,
            "assembly_finished": True,
            "basket_quantity": 0,
            "basket_has_items": False,
            "assembly_items": [],
            "unassembled_items": [],
            "basket_items": [],
        }
        results = {
            "smartup": {
                "document_found": True,
                "status_code": "B#W",
                "status": "В ожидании",
                "order": {},
            },
            "warehouse_routing_exact": False,
            "warehouse_codes": ["unknown"],
            "warehouse_names": [],
            "regions": [
                {"name": "Недоступный", "selected": True, "wms": {"_error": "timeout"}},
                {
                    "name": "Наманган",
                    "selected": True,
                    "wms": good_wms,
                    "series_check": {"available": True, "matches": True, "issues": [], "items": []},
                },
            ],
            "_elapsed": 0,
        }
        configured = [
            {"name": "Недоступный"},
            {"name": "Наманган", "warehouse_name": "Склад Наманган"},
        ]

        bot.apply_wms_fallback_routing(results, configured)
        text = bot.format_result("42", results)

        self.assertNotIn("Склад определён по данным WMS", text)
        self.assertIn("📍 Регион: Наманган", text)
        self.assertNotIn("Недоступный", text)
        self.assertNotIn("timeout", text)

    def test_wms_fallback_does_not_duplicate_smartup_warehouse_name(self):
        results = {
            "warehouse_routing_exact": False,
            "warehouse_names": ["Склад_ЛПУ_Карши"],
            "regions": [{
                "name": "Карши", "selected": True,
                "wms": {"document_found": True},
            }],
        }
        configured = [{"name": "Карши", "warehouse_name": "Склад Карши"}]

        bot.apply_wms_fallback_routing(results, configured)

        self.assertEqual(results["warehouse_names"], ["Склад_ЛПУ_Карши"])
        self.assertEqual(results["wms_matched_regions"], ["Карши"])

    def test_processing_order_has_pending_outcome_in_both_languages(self):
        for language, expected in (("ru", "ИТОГ: ЗАЯВКА В РАБОТЕ"), ("uz", "YAKUN: ARIZA JARAYONDA")):
            with self.subTest(language=language):
                results = {
                    "smartup": {
                        "document_found": True,
                        "status_code": "B#E",
                        "status": "В обработке",
                        "order": {},
                    },
                    "warehouse_codes": [],
                    "warehouse_names": [],
                    "_elapsed": 0,
                }

                text = bot.format_result("42", results, language)

                self.assertIn(expected, text)
                self.assertEqual(results["_summary"]["outcome"], "pending")

    def test_future_delivery_date_explains_why_delivered_order_is_not_archived(self):
        results = {
            "smartup": {
                "document_found": True,
                "status_code": "B#V",
                "status": "Доставлен",
                "order": {"delivery_date": "31.12.2999"},
            },
            "warehouse_routing_exact": True,
            "warehouse_codes": [],
            "warehouse_names": ["Склад Тест"],
            "regions": [{
                "name": "Тест",
                "selected": True,
                "wms_responsible": "Склад Тест",
                "wms": {
                    "document_found": True,
                    "assembly_found": True,
                    "assembled": True,
                    "assembly_finished": True,
                    "assembly_items": [],
                    "unassembled_items": [],
                },
                "series_check": {
                    "available": True, "matches": True, "issues": [], "items": [],
                },
            }],
            "_elapsed": 0,
        }

        russian = bot.format_result("42", results, "ru")
        uzbek = bot.format_result("42", results, "uz")

        self.assertIn("⚠️ Ещё не доставлен", russian)
        self.assertIn("📅 Дата доставки: 31.12.2999", russian)
        self.assertIn("Дождаться доставки", russian)
        self.assertIn("⚠️ Hali yetkazilmagan", uzbek)
        self.assertIn("📅 Yetkazib berish sanasi: 31.12.2999", uzbek)
        self.assertIn("Yetkazib berishni kutish", uzbek)

    def test_delivery_date_is_future_only_after_tashkent_calendar_day(self):
        today = bot.datetime(2026, 11, 13).date()

        self.assertEqual(bot.future_delivery_date_text("14.11.2026", today), "14.11.2026")
        self.assertEqual(bot.future_delivery_date_text("13.11.2026", today), "")
        self.assertEqual(bot.future_delivery_date_text("12.11.2026", today), "")

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
        self.assertEqual(result["items"][0]["problem"], "Собрано меньше, чем нужно")

        wms_result = {
            "document_found": True,
            "assembly_found": True,
            "assembled": True,
            "assembly_finished": True,
            "basket_quantity": 0,
            "basket_has_items": False,
            "assembly_items": wms["assembly_items"],
            "unassembled_items": [],
            "basket_items": [],
        }
        lines, problems, unavailable = bot.format_wms(wms_result, "B#V", series_check=result)
        self.assertIn("📦 Товаров с расхождениями: 1 — таблица ниже", lines)
        self.assertFalse(any("Количество не совпало: Test product" in line for line in lines))
        self.assertEqual((problems, unavailable), (1, 0))

    def test_deleted_byas_is_not_treated_as_an_active_assembly(self):
        wms = {
            "document_found": True,
            "assembly_found": True,
            "assembly_marked_for_deletion": True,
            "assembled": True,
            "assembly_finished": True,
            "basket_has_items": False,
            "basket_quantity": 0,
            "assembly_items": [],
            "unassembled_items": [],
            "basket_items": [],
        }

        normalized = bot.normalize_wms(wms)
        lines, problems, unavailable = bot.format_wms(wms, "B#V")
        reason, action, _ = bot.wms_summary(wms, "B#V")

        self.assertFalse(normalized["assembly_found"])
        self.assertFalse(normalized["assembled"])
        self.assertTrue(any("БЯС помечена на удаление" in line for line in lines))
        self.assertEqual((problems, unavailable), (1, 0))
        self.assertEqual(reason, "БЯС помечена на удаление")
        self.assertIn("действующую БЯС", action)

    def test_missing_byas_uses_safe_wording_when_wms_has_no_deletion_flag(self):
        wms = {
            "document_found": True,
            "assembly_found": False,
            "assembled": False,
            "basket_has_items": False,
            "basket_quantity": 0,
            "assembly_items": [],
            "unassembled_items": [],
            "basket_items": [],
        }

        reason, _, _ = bot.wms_summary(wms, "B#V")

        self.assertIn("не выбрана или помечена на удаление", reason)

    def test_basket_payload_does_not_affect_wms_result_or_tables(self):
        wms = {
            "document_found": True,
            "assembly_found": True,
            "assembled": True,
            "assembly_finished": True,
            "basket_has_items": "invalid and ignored",
            "basket_quantity": "invalid and ignored",
            "basket_items": "invalid and ignored",
            "assembly_items": [],
            "unassembled_items": [],
        }

        normalized = bot.normalize_wms(wms)
        lines, _, unavailable = bot.format_wms(wms, "B#V", series_check={
            "available": True, "matches": True, "issues": [], "items": [],
        })
        tables = bot.collect_wms_tables({
            "regions": [{"selected": True, "name": "Test", "wms": normalized}],
        })

        self.assertNotIn("_error", normalized)
        self.assertTrue(normalized["assembled"])
        self.assertFalse(any("корзин" in line.casefold() for line in lines))
        self.assertEqual(unavailable, 0)
        self.assertEqual(tables, [])

    def test_fully_returned_item_assembled_in_byas_is_an_error(self):
        smartup = {
            "order": {
                "order_products": [{
                    "product_code": "A1",
                    "product_name": "Returned product",
                    "card_code": "S1",
                    "order_quant": 2,
                    "sold_quant": 0,
                    "return_quant": 2,
                }]
            }
        }
        wms = {
            "assembly_items_available": True,
            "assembly_items": [{
                "product_code": "A1",
                "product_name": "Returned product",
                "series": "S1",
                "quantity": 2,
            }],
        }

        result = bot.compare_series(smartup, wms)

        self.assertTrue(result["available"])
        self.assertFalse(result["matches"])
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["problem"], "Собрано больше, чем нужно")
        self.assertEqual(result["items"][0]["smartup_quantity"], "0")
        self.assertEqual(result["items"][0]["wms_quantity"], "2")

    def test_partial_return_compares_wms_with_quantity_that_must_be_assembled(self):
        smartup = {
            "order": {
                "order_products": [{
                    "product_code": "A1",
                    "product_name": "Partially returned product",
                    "card_code": "S1",
                    "order_quant": 10,
                    "sold_quant": 6,
                    "return_quant": 4,
                }]
            }
        }
        wms = {
            "assembly_items_available": True,
            "assembly_items": [{
                "product_code": "A1",
                "product_name": "Partially returned product",
                "series": "S1",
                "quantity": 8,
            }],
        }

        result = bot.compare_series(smartup, wms)

        self.assertFalse(result["matches"])
        self.assertEqual(result["items"][0]["smartup_quantity"], "6")
        self.assertEqual(result["items"][0]["wms_quantity"], "8")
        self.assertEqual(result["items"][0]["problem"], "Собрано больше, чем нужно")

    def test_one_hundred_ordered_twenty_returned_requires_eighty_in_wms(self):
        smartup = {
            "order": {
                "order_products": [{
                    "product_code": "A1",
                    "product_name": "Returned quantity example",
                    "card_code": "S1",
                    "order_quant": 100,
                    "sold_quant": 80,
                    "return_quant": 20,
                }]
            }
        }
        wms = {
            "assembly_items_available": True,
            "assembly_items": [{
                "product_code": "A1",
                "product_name": "Returned quantity example",
                "series": "S1",
                "quantity": 80,
            }],
        }

        result = bot.compare_series(smartup, wms)

        self.assertTrue(result["matches"])
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["items"], [])

    def test_smartup_action_quantity_is_added_to_regular_wms_quantity(self):
        smartup = {
            "order": {
                "order_products": [{
                    "product_code": "A1", "product_name": "Promo product",
                    "card_code": "S1", "order_quant": 16,
                    "sold_quant": 16, "return_quant": 0,
                }],
                "order_actions": [{
                    "product_code": "A1", "product_name": "Promo product",
                    "card_code": None, "order_quant": 5,
                    "sold_quant": 5, "return_quant": 0,
                    "bonus_id": "72676", "action_name": "3+1",
                    "details": [{"card_code": "S1", "sold_quant": 5}],
                }],
                "order_gifts": [],
            }
        }
        wms = {
            "assembly_items_available": True,
            "assembly_items": [{
                "product_code": "A1", "product_name": "Promo product",
                "series": "S1", "quantity": 21,
            }],
        }

        result = bot.compare_series(smartup, wms)

        self.assertTrue(result["matches"])
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["items"], [])

    def test_wms_product_missing_from_smartup_has_clear_problem_text(self):
        smartup = {
            "order": {
                "order_products": [{
                    "product_code": "A1", "product_name": "Expected",
                    "card_code": "S1", "sold_quant": 1,
                }]
            }
        }
        wms = {
            "assembly_items_available": True,
            "assembly_items": [{
                "product_code": "A2", "product_name": "WMS only",
                "series": "S2", "quantity": 3,
            }],
        }

        result = bot.compare_series(smartup, wms)
        wms_only = next(item for item in result["items"] if item["product_code"] == "A2")

        self.assertEqual(wms_only["smartup_quantity"], "—")
        self.assertEqual(wms_only["wms_quantity"], "3")
        self.assertEqual(wms_only["problem"], "Товар отсутствует в Smartup")

    def test_different_wms_series_for_smartup_product_stays_extra_series(self):
        smartup = {
            "order": {
                "order_products": [{
                    "product_code": "A1", "product_name": "Expected",
                    "card_code": "S1", "sold_quant": 1,
                }]
            }
        }
        wms = {
            "assembly_items_available": True,
            "assembly_items": [{
                "product_code": "A1", "product_name": "Expected",
                "series": "S2", "quantity": 1,
            }],
        }

        result = bot.compare_series(smartup, wms)
        extra_series = next(item for item in result["items"] if item["series"] == "S2")

        self.assertEqual(extra_series["problem"], "Лишняя серия в сборке")

    def test_series_comparison_reconstructs_original_quantity_when_order_quantity_is_missing(self):
        smartup = {
            "order": {
                "order_products": [{
                    "product_code": "A1",
                    "product_name": "Partially returned product",
                    "card_code": "S1",
                    "sold_quant": 3,
                    "return_quant": 2,
                }]
            }
        }
        wms = {
            "assembly_items_available": True,
            "assembly_items": [{
                "product_code": "A1",
                "product_name": "Partially returned product",
                "series": "S1",
                "quantity": 3,
            }],
        }

        result = bot.compare_series(smartup, wms)

        self.assertTrue(result["matches"])
        self.assertEqual(result["issues"], [])

    def test_fully_returned_unassembled_item_finishes_wms_assembly(self):
        smartup = {
            "order": {"order_products": [{
                "product_code": "A67882", "product_name": "Fully returned",
                "card_code": "01072026", "order_quant": 20,
                "sold_quant": 0, "return_quant": 20,
            }]}
        }
        wms = {
            "document_found": True, "assembly_found": True, "assembled": False,
            "assembly_finished": False, "basket_has_items": False, "basket_quantity": 0,
            "assembly_items": [], "basket_items": [],
            "unassembled_items": [{
                "product_code": "A67882", "product_name": "Fully returned",
                "series": "", "remaining_quantity": 20,
            }],
        }

        reconciled = bot.reconcile_wms_unassembled_returns(smartup, bot.normalize_wms(wms))
        comparison = bot.compare_series(smartup, reconciled)

        self.assertTrue(reconciled["assembly_finished"])
        self.assertTrue(reconciled["assembled"])
        self.assertEqual(reconciled["unassembled_items"], [])
        self.assertEqual(reconciled["returned_unassembled_items"][0]["remaining_quantity"], 20)
        self.assertTrue(comparison["matches"])

    def test_partial_return_reduces_but_does_not_hide_real_unassembled_quantity(self):
        smartup = {
            "order": {"order_products": [{
                "product_code": "A1", "product_name": "Partial return",
                "card_code": "S1", "order_quant": 100,
                "sold_quant": 80, "return_quant": 20,
            }]}
        }
        wms = {
            "document_found": True, "assembly_found": True, "assembled": False,
            "assembly_finished": False, "basket_has_items": False, "basket_quantity": 0,
            "assembly_items": [], "basket_items": [],
            "unassembled_items": [{
                "product_code": "A1", "product_name": "Partial return",
                "series": "S1", "remaining_quantity": 30,
            }],
        }

        reconciled = bot.reconcile_wms_unassembled_returns(smartup, bot.normalize_wms(wms))

        self.assertFalse(reconciled["assembly_finished"])
        self.assertEqual(reconciled["unassembled_items"][0]["remaining_quantity"], 10)
        self.assertEqual(reconciled["returned_unassembled_items"][0]["remaining_quantity"], 20)

    def test_blank_assembly_series_is_reported_without_false_missing_item(self):
        smartup = {
            "order": {"order_products": [{
                "product_code": "A1", "product_name": "No series product",
                "card_code": "S1", "order_quant": 2, "sold_quant": 2,
                "return_quant": 0,
            }]}
        }
        wms = {
            "assembly_items_available": True,
            "assembly_items": [{
                "product_code": "A1", "product_name": "No series product",
                "series": "", "quantity": 2,
            }],
        }

        result = bot.compare_series(smartup, wms)

        self.assertFalse(result["matches"])
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["problem"], "В WMS не заполнен номер серии")
        self.assertEqual(result["items"][0]["smartup_quantity"], "2")
        self.assertEqual(result["items"][0]["wms_quantity"], "2")
        self.assertFalse(any("Нет в сборке" in issue for issue in result["issues"]))

    def test_blank_smartup_series_is_a_directory_error_not_extra_wms_series(self):
        smartup = {
            "order": {"order_products": [{
                "product_code": "A1", "product_name": "Smartup blank series",
                "card_code": "", "order_quant": 2, "sold_quant": 2,
                "return_quant": 0,
            }]}
        }
        wms = {
            "assembly_items_available": True,
            "assembly_items": [{
                "product_code": "A1", "product_name": "Smartup blank series",
                "series": "S1", "quantity": 2,
            }],
        }

        result = bot.compare_series(smartup, wms)

        self.assertFalse(result["matches"])
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["problem"], "В Smartup не заполнен номер серии")
        self.assertEqual(result["items"][0]["series"], "S1")
        self.assertEqual(result["items"][0]["smartup_quantity"], "2")
        self.assertEqual(result["items"][0]["wms_quantity"], "2")
        self.assertFalse(any("Лишняя серия в сборке" in issue for issue in result["issues"]))

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

    def test_tms_card_shows_transport_and_ttn_failure_reason(self):
        tms = {
            "order_found": True,
            "driver": "Иванов И.И.",
            "transport": "Isuzu 01 A 123 BC",
            "ttn_sent": False,
            "ttn_error": "Не заполнен номер доверенности",
        }
        result = bot.format_tms_card(tms, "Ташкент", "Логистика Ташкент")

        self.assertIn("🚚 Отгрузка", result["text"])
        self.assertNotIn("Ташкент", result["text"])
        self.assertIn("👤 Водитель: Иванов И.И.", result["text"])
        self.assertIn("🚛 Транспорт: Isuzu 01 A 123 BC", result["text"])
        self.assertIn("❌ ТТН не прошла", result["text"])
        self.assertIn("⚠️ Причина:\n<b>Не заполнен номер доверенности</b>", result["text"])
        self.assertEqual(result["problems"], 1)
        self.assertEqual(result["owner"], "Логистика Ташкент")

        uzbek = bot.format_tms_card(tms, "Toshkent", "Toshkent logistikasi", "uz")["text"]
        self.assertIn("👤 Haydovchi: Иванов И.И.", uzbek)
        self.assertIn("🚛 Transport: Isuzu 01 A 123 BC", uzbek)
        self.assertIn("❌ TTN o'tmadi", uzbek)
        self.assertIn("⚠️ Sabab:\n<b>Не заполнен номер доверенности</b>", uzbek)

        escaped = bot.format_tms_card({
            "order_found": True,
            "ttn_sent": False,
            "ttn_error": "Ошибка <ТТН> & повтор",
        }, "Ташкент")["text"]
        self.assertIn("<b>Ошибка &lt;ТТН&gt; &amp; повтор</b>", escaped)

    def test_successful_ttn_keeps_old_tms_contract_compatible(self):
        result = bot.format_tms_card({
            "order_found": True,
            "driver": "Иванов И.И.",
            "ttn_sent": True,
        }, "Наманган")

        self.assertIn("Транспорт не передан сервисом TMS", result["text"])
        self.assertIn("✅ ТТН отправлена", result["text"])
        self.assertFalse(result["send"])
        self.assertEqual(result["problems"], 0)

    def test_tms_card_reports_missing_waybill_without_false_field_warnings(self):
        result = bot.format_tms_card({
            "order_found": True,
            "waybill_found": False,
            "driver": "",
            "transport": "",
            "ttn_sent": False,
            "ttn_error": "",
        }, "Наманган", "Логистика Наманган")

        self.assertEqual(result["text"], "❌ TMS: путевой лист не привязан")
        self.assertTrue(result["send"])
        self.assertEqual(result["problems"], 1)
        self.assertEqual(result["action"], "Логистика должна привязать путевой лист к отгрузке")

    def test_tms_fallback_uses_one_aggregate_card_when_document_is_not_found(self):
        results = {
            "warehouse_routing_exact": False,
            "regions": [
                {"name": "Ташкент", "selected": True, "tms": {"order_found": False}},
                {"name": "Наманган", "selected": True, "tms": {"order_found": False}},
            ],
        }

        bot.apply_tms_fallback_routing(results, [{}, {}])

        self.assertIn("tms_fallback_result", results)
        self.assertFalse(any(item["selected"] for item in results["regions"]))

    def test_accounting_stage_builds_separate_tms_card_without_changing_summary(self):
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
                "invoice_status": "Подписан",
                "invoice_sent": True,
            },
            "regions": [{
                "name": "Ташкент",
                "selected": True,
                "tms_responsible": "Логистика Ташкент",
                "tms": {
                    "order_found": True,
                    "driver": "Иванов И.И.",
                    "transport": "Isuzu 01 A 123 BC",
                    "ttn_sent": False,
                    "ttn_error": "Ошибка доверенности",
                },
            }],
            "warehouse_codes": ["4"],
            "warehouse_names": ["Склад Ташкент"],
            "_elapsed": 0,
        }

        text = bot.format_result("123", results)

        self.assertNotIn("🚚 Отгрузка", text)
        self.assertEqual(len(results["_mini_messages"]), 1)
        self.assertIn("Ошибка доверенности", results["_mini_messages"][0])
        self.assertEqual(results["_summary"]["outcome"], "ok")
        self.assertNotIn("ТТН не прошла: Ошибка доверенности", results["_summary"]["problem_reasons"])

    def test_successful_ttn_does_not_build_a_separate_message(self):
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
                "invoice_status": "Подписан",
                "invoice_sent": True,
            },
            "regions": [{
                "name": "Наманган",
                "selected": True,
                "tms": {
                    "order_found": True,
                    "driver": "Иванов И.И.",
                    "ttn_sent": True,
                },
            }],
            "warehouse_codes": ["8"],
            "warehouse_names": ["Склад Наманган"],
            "_elapsed": 0,
        }

        bot.format_result("4544666", results)

        self.assertEqual(results["_mini_messages"], [])
        self.assertEqual(results["_summary"]["outcome"], "ok")

    def test_tms_unavailable_is_separate_and_does_not_change_accounting_summary(self):
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
                "invoice_status": "Подписан",
                "invoice_sent": True,
            },
            "regions": [{
                "name": "Ташкент",
                "selected": True,
                "tms": {"_error": "timeout"},
            }],
            "warehouse_codes": ["4"],
            "warehouse_names": ["Склад Ташкент"],
            "_elapsed": 0,
        }

        text = bot.format_result("123", results)

        self.assertIn("✅ ИТОГ: ОШИБОК НЕ НАЙДЕНО", text)
        self.assertEqual(results["_summary"]["outcome"], "ok")
        self.assertEqual(len(results["_mini_messages"]), 1)
        self.assertIn("TMS недоступна", results["_mini_messages"][0])

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

    def test_tms_mini_message_is_sent_with_html_formatting(self):
        calls = []
        old_telegram_call = bot.telegram_call
        old_check_orders = bot.check_orders
        old_save_check_audit = bot.save_check_audit

        try:
            bot.telegram_call = lambda token, method, params, timeout=40: calls.append((method, dict(params))) or {}
            bot.check_orders = lambda config, numbers, language="ru": [{
                "text": "Заявка 1",
                "mini_messages": ["⚠️ Причина:\n<b>Ошибка ТТН</b>"],
                "tables": [],
                "summary": {},
            }]
            bot.save_check_audit = lambda *args: None
            message = {"message_id": 10, "chat": {"id": 20}, "from": {"id": 30}}

            bot.send_batch_check({"telegram_bot_token": "token"}, message, ["1"], "ru")

            mini = next(params for method, params in calls if method == "sendMessage" and "Ошибка ТТН" in params.get("text", ""))
            self.assertEqual(mini["parse_mode"], "HTML")
        finally:
            bot.telegram_call = old_telegram_call
            bot.check_orders = old_check_orders
            bot.save_check_audit = old_save_check_audit


if __name__ == "__main__":
    unittest.main()
