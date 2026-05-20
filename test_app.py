"""Testes automatizados do webhook e das regras centrais do bot."""

# Bibliotecas padrão usadas para montar assinaturas e mocks dos cenários de teste.
import hashlib
import hmac
import json
import os
import unittest
from unittest.mock import Mock, patch

# Dependência usada para simular a exceção HTTP disparada pelo `requests`.
import requests


# Define variáveis de ambiente controladas antes de importar a aplicação.
os.environ["META_APP_SECRET"] = "test_app_secret"
os.environ["WHATSAPP_VERIFY_TOKEN"] = "meu-token-de-verificacao"
os.environ["WHATSAPP_TOKEN"] = "test_token"
os.environ["WHATSAPP_PHONE_NUMBER_ID"] = "123456789"
os.environ["HUMAN_TAKEOVER_TOKEN"] = "test_takeover_token"

# Importa o catálogo real de mensagens usado pelo runtime.
from messages import (
    DEFAULT_MESSAGE,
    INITIAL_MENU_BUTTONS,
    OTHER_MENU_BODY,
    OTHER_MENU_BUTTON_TEXT,
    OTHER_MENU_ROWS,
    PREDEFINED_MESSAGES,
    WELCOME_MESSAGE,
)

# Importa o app já configurado com os valores de teste acima.
from app import (
    app,
    get_request_client_ip,
    reset_processed_message_ids,
    reset_human_takeovers,
    reset_webhook_rate_limits,
    send_whatsapp_button_message,
    send_whatsapp_list_message,
    validate_runtime_configuration,
)


def json_bytes(payload: dict) -> bytes:
    """Serializa o payload em bytes, preservando o formato usado na assinatura."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def meta_signature(payload: bytes) -> str:
    """Reproduz a assinatura HMAC que a Meta enviaria no header do webhook."""
    digest = hmac.new(
        os.environ["META_APP_SECRET"].encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"


class WhatsAppBotTests(unittest.TestCase):
    """Cobertura principal do fluxo de webhook do bot."""

    def setUp(self):
        """Reinicia o estado em memória e cria o cliente de teste do Flask."""
        reset_processed_message_ids()
        reset_human_takeovers()
        reset_webhook_rate_limits()
        self.client = app.test_client()

    def admin_headers(self, token: str = "test_takeover_token") -> dict[str, str]:
        """Monta o header simples usado para controlar o takeover manual."""
        return {"X-Admin-Token": token}

    def signed_post(
        self,
        payload: dict,
        headers: dict | None = None,
        remote_addr: str | None = None,
    ):
        """Envia um POST assinado do mesmo jeito que a Meta enviaria."""
        payload_bytes = json_bytes(payload)
        final_headers = {"X-Hub-Signature-256": meta_signature(payload_bytes)}
        if headers:
            final_headers.update(headers)

        environ_overrides = {}
        if remote_addr:
            environ_overrides["REMOTE_ADDR"] = remote_addr

        return self.client.post(
            "/webhook",
            data=payload_bytes,
            content_type="application/json",
            headers=final_headers,
            environ_overrides=environ_overrides,
        )

    def assert_initial_menu_payload(self, payload: dict) -> None:
        """Confere o payload interativo do menu inicial."""
        self.assertEqual(payload["type"], "interactive")
        interactive = payload["interactive"]
        self.assertEqual(interactive["type"], "button")
        self.assertEqual(interactive["body"]["text"], WELCOME_MESSAGE)
        self.assertEqual(
            interactive["action"]["buttons"],
            [
                {
                    "type": "reply",
                    "reply": {"id": button["id"], "title": button["title"]},
                }
                for button in INITIAL_MENU_BUTTONS
            ],
        )

    def assert_text_payload(self, payload: dict, expected_text: str) -> None:
        """Confere payload simples de texto."""
        self.assertEqual(payload, {"type": "text", "text": {"body": expected_text}})

    def assert_other_options_payload(self, payload: dict) -> None:
        """Confere o payload interativo da lista de outros assuntos."""
        self.assertEqual(payload["type"], "interactive")
        interactive = payload["interactive"]
        self.assertEqual(interactive["type"], "list")
        self.assertEqual(interactive["body"]["text"], OTHER_MENU_BODY)
        self.assertEqual(interactive["action"]["button"], OTHER_MENU_BUTTON_TEXT)
        rows = interactive["action"]["sections"][0]["rows"]
        self.assertEqual(
            rows,
            [
                {
                    key: value
                    for key, value in {
                        "id": row["id"],
                        "title": row["title"],
                        "description": row.get("description"),
                    }.items()
                    if value
                }
                for row in OTHER_MENU_ROWS
            ],
        )

    def test_healthcheck(self):
        """Confirma que o healthcheck responde `200` e status `ok`."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["status"], "ok")

    def test_webhook_verification_success(self):
        """Garante que o GET do webhook devolve o challenge quando o token bate."""
        response = self.client.get(
            "/webhook?hub.mode=subscribe&hub.verify_token=meu-token-de-verificacao&hub.challenge=12345"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.decode(), "12345")

    def test_webhook_verification_failure(self):
        """Garante que a verificação falha quando o token não confere."""
        response = self.client.get(
            "/webhook?hub.mode=subscribe&hub.verify_token=errado&hub.challenge=12345"
        )
        self.assertEqual(response.status_code, 403)

    @patch("app.requests.post")
    def test_send_whatsapp_button_message_builds_meta_payload(self, post_mock):
        """Monta payload Meta para botoes interativos."""
        response_mock = Mock()
        response_mock.json.return_value = {"messages": [{"id": "wamid-button"}]}
        post_mock.return_value = response_mock

        send_whatsapp_button_message("5511999999999", WELCOME_MESSAGE, INITIAL_MENU_BUTTONS)

        response_mock.raise_for_status.assert_called_once()
        payload = post_mock.call_args.kwargs["json"]
        self.assertEqual(payload["messaging_product"], "whatsapp")
        self.assertEqual(payload["to"], "5511999999999")
        self.assert_initial_menu_payload(
            {
                key: value
                for key, value in payload.items()
                if key not in {"messaging_product", "to"}
            }
        )

    @patch("app.requests.post")
    def test_send_whatsapp_list_message_builds_meta_payload(self, post_mock):
        """Monta payload Meta para lista interativa."""
        response_mock = Mock()
        response_mock.json.return_value = {"messages": [{"id": "wamid-list"}]}
        post_mock.return_value = response_mock

        send_whatsapp_list_message(
            "5511999999999",
            OTHER_MENU_BODY,
            OTHER_MENU_BUTTON_TEXT,
            OTHER_MENU_ROWS,
        )

        response_mock.raise_for_status.assert_called_once()
        payload = post_mock.call_args.kwargs["json"]
        self.assertEqual(payload["messaging_product"], "whatsapp")
        self.assertEqual(payload["to"], "5511999999999")
        self.assert_other_options_payload(
            {
                key: value
                for key, value in payload.items()
                if key not in {"messaging_product", "to"}
            }
        )

    def test_validate_runtime_configuration_requires_webhook_secrets_outside_debug(self):
        """Falha cedo fora do debug quando segredos críticos do webhook não existem."""
        with patch("app.VERIFY_TOKEN", ""), patch("app.APP_SECRET", ""):
            with self.assertRaisesRegex(RuntimeError, "META_APP_SECRET"):
                validate_runtime_configuration(debug=False)

    def test_validate_runtime_configuration_rejects_invalid_webhook_ip_config(self):
        """Falha cedo quando a configuração de IP/CIDR do webhook é inválida."""
        with patch("app.WEBHOOK_ALLOWED_IPS", "ip-invalido"):
            with self.assertRaisesRegex(RuntimeError, "WEBHOOK_ALLOWED_IPS"):
                validate_runtime_configuration(debug=False)

    def test_get_request_client_ip_uses_remote_addr_without_trusted_proxy(self):
        """Ignora `X-Forwarded-For` quando o proxy remoto não é confiável."""
        with app.test_request_context(
            "/webhook",
            method="POST",
            headers={"X-Forwarded-For": "198.51.100.10"},
            environ_overrides={"REMOTE_ADDR": "203.0.113.10"},
        ):
            with patch("app.TRUSTED_PROXY_IPS", ""):
                self.assertEqual(get_request_client_ip(), "203.0.113.10")

    def test_get_request_client_ip_uses_forwarded_chain_from_trusted_proxy(self):
        """Aceita a cadeia `X-Forwarded-For` só quando o `REMOTE_ADDR` é confiável."""
        with app.test_request_context(
            "/webhook",
            method="POST",
            headers={"X-Forwarded-For": "198.51.100.10, 203.0.113.20"},
            environ_overrides={"REMOTE_ADDR": "203.0.113.30"},
        ):
            with patch("app.TRUSTED_PROXY_IPS", "203.0.113.0/24"):
                self.assertEqual(get_request_client_ip(), "198.51.100.10")

    def test_post_webhook_rejects_invalid_signature(self):
        """Recusa eventos com assinatura HMAC inválida."""
        response = self.client.post(
            "/webhook",
            json={"entry": []},
            headers={"X-Hub-Signature-256": "sha256=invalida"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json["detail"], "invalid signature")

    def test_human_takeover_requires_valid_admin_token(self):
        """Protege o takeover manual com um token administrativo simples."""
        response = self.client.post(
            "/human-takeover",
            json={"phone": "5511999999999"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json["detail"], "invalid admin token")

    def test_human_takeover_can_activate_query_and_release_phone(self):
        """Permite ativar, consultar e liberar o takeover manual por numero."""
        activate_response = self.client.post(
            "/human-takeover",
            json={"phone": "+55 (11) 99999-9999"},
            headers=self.admin_headers(),
        )
        status_response = self.client.get(
            "/human-takeover?phone=+55 (11) 99999-9999",
            headers=self.admin_headers(),
        )
        release_response = self.client.delete(
            "/human-takeover",
            json={"phone": "5511999999999"},
            headers=self.admin_headers(),
        )

        self.assertEqual(activate_response.status_code, 200)
        self.assertEqual(activate_response.json["phone"], "5511999999999")
        self.assertTrue(activate_response.json["human_takeover_active"])

        self.assertEqual(status_response.status_code, 200)
        self.assertTrue(status_response.json["human_takeover_active"])

        self.assertEqual(release_response.status_code, 200)
        self.assertFalse(release_response.json["human_takeover_active"])

    @patch("app.send_whatsapp_message_payload")
    def test_post_webhook_silences_bot_during_human_takeover(self, send_message_mock):
        """Nao responde automaticamente enquanto o numero estiver sob takeover."""
        self.client.post(
            "/human-takeover",
            json={"phone": "5511999999999"},
            headers=self.admin_headers(),
        )

        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {"phone_number_id": "123456789"},
                                "messages": [
                                    {
                                        "id": "wamid-human-takeover",
                                        "from": "5511999999999",
                                        "type": "text",
                                        "text": {"body": "menu"},
                                    }
                                ],
                            },
                        }
                    ]
                }
            ],
        }

        response = self.signed_post(payload)

        self.assertEqual(response.status_code, 200)
        send_message_mock.assert_not_called()

    @patch("app.send_whatsapp_message_payload")
    def test_post_webhook_resumes_after_human_takeover_release(self, send_message_mock):
        """Volta a responder quando o takeover manual do numero eh removido."""
        self.client.post(
            "/human-takeover",
            json={"phone": "5511999999999"},
            headers=self.admin_headers(),
        )
        self.client.delete(
            "/human-takeover",
            json={"phone": "5511999999999"},
            headers=self.admin_headers(),
        )

        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {"phone_number_id": "123456789"},
                                "messages": [
                                    {
                                        "id": "wamid-after-human-takeover",
                                        "from": "5511999999999",
                                        "type": "text",
                                        "text": {"body": "menu"},
                                    }
                                ],
                            },
                        }
                    ]
                }
            ],
        }

        response = self.signed_post(payload)

        self.assertEqual(response.status_code, 200)
        send_message_mock.assert_called_once()
        to_phone, payload = send_message_mock.call_args.args
        self.assertEqual(to_phone, "5511999999999")
        self.assert_initial_menu_payload(payload)

    @patch("app.send_whatsapp_message_payload")
    def test_post_webhook_sends_initial_clickable_menu(self, send_message_mock):
        """Saudacao/menu abre o menu inicial por botoes."""
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {"phone_number_id": "123456789"},
                                "messages": [
                                    {
                                        "id": "wamid-1",
                                        "from": "5511999999999",
                                        "type": "text",
                                        "text": {"body": "menu"},
                                    }
                                ],
                            },
                        }
                    ]
                }
            ],
        }

        response = self.signed_post(payload)

        self.assertEqual(response.status_code, 200)
        send_message_mock.assert_called_once()
        to_phone, payload = send_message_mock.call_args.args
        self.assertEqual(to_phone, "5511999999999")
        self.assert_initial_menu_payload(payload)

    @patch("app.send_whatsapp_message_payload")
    def test_post_webhook_matches_greeting_ignoring_case(self, send_message_mock):
        """Aceita saudações como `Boa noite` sem depender de caixa alta ou baixa."""
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {"phone_number_id": "123456789"},
                                "messages": [
                                    {
                                        "id": "wamid-greeting-case",
                                        "from": "5511999999999",
                                        "type": "text",
                                        "text": {"body": "Boa noite"},
                                    }
                                ],
                            },
                        }
                    ]
                }
            ],
        }

        response = self.signed_post(payload)

        self.assertEqual(response.status_code, 200)
        send_message_mock.assert_called_once()
        to_phone, payload = send_message_mock.call_args.args
        self.assertEqual(to_phone, "5511999999999")
        self.assert_initial_menu_payload(payload)

    @patch("app.send_whatsapp_message_payload")
    def test_post_webhook_routes_button_reply_option_one(self, send_message_mock):
        """Botao inicial `opcao_1` dispara a resposta 1."""
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {"phone_number_id": "123456789"},
                                "messages": [
                                    {
                                        "id": "wamid-button-1",
                                        "from": "5511999999999",
                                        "type": "interactive",
                                        "interactive": {
                                            "type": "button_reply",
                                            "button_reply": {
                                                "id": "opcao_1",
                                                "title": "Primeira Consulta",
                                            },
                                        },
                                    }
                                ],
                            },
                        }
                    ]
                }
            ],
        }

        response = self.signed_post(payload)

        self.assertEqual(response.status_code, 200)
        send_message_mock.assert_called_once()
        to_phone, payload = send_message_mock.call_args.args
        self.assertEqual(to_phone, "5511999999999")
        self.assert_text_payload(payload, PREDEFINED_MESSAGES["1"])

    @patch("app.send_whatsapp_message_payload")
    def test_post_webhook_routes_button_reply_others_to_list(self, send_message_mock):
        """Botao `outros` abre a lista clicavel."""
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {"phone_number_id": "123456789"},
                                "messages": [
                                    {
                                        "id": "wamid-button-outros",
                                        "from": "5511999999999",
                                        "type": "interactive",
                                        "interactive": {
                                            "type": "button_reply",
                                            "button_reply": {
                                                "id": "outros",
                                                "title": "Outros assuntos",
                                            },
                                        },
                                    }
                                ],
                            },
                        }
                    ]
                }
            ],
        }

        response = self.signed_post(payload)

        self.assertEqual(response.status_code, 200)
        send_message_mock.assert_called_once()
        to_phone, payload = send_message_mock.call_args.args
        self.assertEqual(to_phone, "5511999999999")
        self.assert_other_options_payload(payload)

    @patch("app.send_whatsapp_message_payload")
    def test_post_webhook_routes_list_reply_to_existing_answer(self, send_message_mock):
        """Item da lista dispara a resposta correspondente do menu antigo."""
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {"phone_number_id": "123456789"},
                                "messages": [
                                    {
                                        "id": "wamid-list-6",
                                        "from": "5511999999999",
                                        "type": "interactive",
                                        "interactive": {
                                            "type": "list_reply",
                                            "list_reply": {
                                                "id": "menu_item_6",
                                                "title": "Receita/relatórios",
                                            },
                                        },
                                    }
                                ],
                            },
                        }
                    ]
                }
            ],
        }

        response = self.signed_post(payload)

        self.assertEqual(response.status_code, 200)
        send_message_mock.assert_called_once()
        to_phone, payload = send_message_mock.call_args.args
        self.assertEqual(to_phone, "5511999999999")
        self.assert_text_payload(payload, PREDEFINED_MESSAGES["6"])

    @patch("app.send_whatsapp_message_payload")
    def test_post_webhook_ignores_status_events(self, send_message_mock):
        """Ignora notificações de status, já que elas não são mensagens de cliente."""
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {"phone_number_id": "123456789"},
                                "statuses": [
                                    {
                                        "id": "status-1",
                                        "status": "delivered",
                                        "recipient_id": "5511999999999",
                                    }
                                ],
                            },
                        }
                    ]
                }
            ],
        }

        response = self.signed_post(payload)

        self.assertEqual(response.status_code, 200)
        send_message_mock.assert_not_called()

    @patch("app.send_whatsapp_message_payload")
    def test_post_webhook_processes_text_message_from_second_change(self, send_message_mock):
        """Não assume mais que a mensagem útil está sempre na primeira change."""
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {"phone_number_id": "123456789"},
                                "statuses": [
                                    {
                                        "id": "status-1",
                                        "status": "delivered",
                                        "recipient_id": "5511999999999",
                                    }
                                ],
                            },
                        },
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {"phone_number_id": "123456789"},
                                "messages": [
                                    {
                                        "id": "wamid-second-change",
                                        "from": "5511999999999",
                                        "type": "text",
                                        "text": {"body": "menu"},
                                    }
                                ],
                            },
                        },
                    ]
                }
            ],
        }

        response = self.signed_post(payload)

        self.assertEqual(response.status_code, 200)
        send_message_mock.assert_called_once()
        to_phone, payload = send_message_mock.call_args.args
        self.assertEqual(to_phone, "5511999999999")
        self.assert_initial_menu_payload(payload)

    @patch("app.send_whatsapp_message_payload")
    def test_post_webhook_processes_multiple_text_messages_in_same_payload(
        self, send_message_mock
    ):
        """Processa todas as mensagens validas dentro do mesmo payload."""
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {"phone_number_id": "123456789"},
                                "messages": [
                                    {
                                        "id": "wamid-batch-1",
                                        "from": "5511999999999",
                                        "type": "text",
                                        "text": {"body": "1"},
                                    },
                                    {
                                        "id": "wamid-batch-2",
                                        "from": "5511888888888",
                                        "type": "text",
                                        "text": {"body": "menu"},
                                    },
                                ],
                            },
                        }
                    ]
                }
            ],
        }

        response = self.signed_post(payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(send_message_mock.call_count, 2)
        sent_payloads = {
            call.args[0]: call.args[1] for call in send_message_mock.call_args_list
        }
        self.assert_text_payload(sent_payloads["5511999999999"], DEFAULT_MESSAGE)
        self.assert_initial_menu_payload(sent_payloads["5511888888888"])

    @patch("app.send_whatsapp_message_payload")
    def test_post_webhook_deduplicates_by_message_id(self, send_message_mock):
        """Processa apenas uma vez o mesmo `message.id` recebido em duplicidade."""
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {"phone_number_id": "123456789"},
                                "messages": [
                                    {
                                        "id": "wamid-duplicate",
                                        "from": "5511999999999",
                                        "type": "text",
                                        "text": {"body": "1"},
                                    }
                                ],
                            },
                        }
                    ]
                }
            ],
        }

        first_response = self.signed_post(payload)
        second_response = self.signed_post(payload)

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        send_message_mock.assert_called_once()

    @patch("app.send_whatsapp_message_payload")
    def test_post_webhook_does_not_route_number_with_text_suffix(self, send_message_mock):
        """Nao usa numero digitado como fluxo principal."""
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {"phone_number_id": "123456789"},
                                "messages": [
                                    {
                                        "id": "wamid-option-4-with-text",
                                        "from": "5511999999999",
                                        "type": "text",
                                        "text": {"body": "4 estou com tontura depois do almoço"},
                                    }
                                ],
                            },
                        }
                    ]
                }
            ],
        }

        response = self.signed_post(payload)

        self.assertEqual(response.status_code, 200)
        send_message_mock.assert_called_once()
        to_phone, payload = send_message_mock.call_args.args
        self.assertEqual(to_phone, "5511999999999")
        self.assert_text_payload(payload, DEFAULT_MESSAGE)

    @patch("app.send_whatsapp_message_payload")
    def test_post_webhook_does_not_confuse_other_numbers_with_menu_options(
        self, send_message_mock
    ):
        """Não trata `45...` como se fosse a opção `4`."""
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {"phone_number_id": "123456789"},
                                "messages": [
                                    {
                                        "id": "wamid-non-menu-number",
                                        "from": "5511999999999",
                                        "type": "text",
                                        "text": {"body": "45 gotas por dia"},
                                    }
                                ],
                            },
                        }
                    ]
                }
            ],
        }

        response = self.signed_post(payload)

        self.assertEqual(response.status_code, 200)
        send_message_mock.assert_called_once()
        to_phone, payload = send_message_mock.call_args.args
        self.assertEqual(to_phone, "5511999999999")
        self.assert_text_payload(payload, DEFAULT_MESSAGE)

    @patch("app.send_whatsapp_message_payload")
    def test_post_webhook_ignores_messages_for_unconfigured_phone_number_id(
        self, send_message_mock
    ):
        """Ignora mensagens que chegaram para outro `phone_number_id`."""
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {"phone_number_id": "999999999"},
                                "messages": [
                                    {
                                        "id": "wamid-wrong-phone-id",
                                        "from": "5511999999999",
                                        "type": "text",
                                        "text": {"body": "menu"},
                                    }
                                ],
                            },
                        }
                    ]
                }
            ],
        }

        response = self.signed_post(payload)

        self.assertEqual(response.status_code, 200)
        send_message_mock.assert_not_called()

    def test_post_webhook_rejects_non_json_content_type(self):
        """Aceita apenas JSON no POST do webhook."""
        payload_bytes = json_bytes({"object": "whatsapp_business_account", "entry": []})
        response = self.client.post(
            "/webhook",
            data=payload_bytes,
            content_type="text/plain",
            headers={"X-Hub-Signature-256": meta_signature(payload_bytes)},
        )

        self.assertEqual(response.status_code, 415)
        self.assertEqual(response.json["detail"], "unsupported media type")

    @patch("app.send_whatsapp_message_payload")
    def test_post_webhook_rejects_ip_outside_allowlist(self, send_message_mock):
        """Bloqueia o webhook quando o IP resolvido não está na allowlist."""
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {"phone_number_id": "123456789"},
                                "messages": [
                                    {
                                        "id": "wamid-allowlist-block",
                                        "from": "5511999999999",
                                        "type": "text",
                                        "text": {"body": "menu"},
                                    }
                                ],
                            },
                        }
                    ]
                }
            ],
        }

        with patch("app.WEBHOOK_ALLOWED_IPS", "198.51.100.10"):
            response = self.signed_post(payload, remote_addr="203.0.113.10")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json["detail"], "forbidden source")
        send_message_mock.assert_not_called()

    @patch("app.send_whatsapp_message_payload")
    def test_post_webhook_allows_trusted_proxy_with_allowed_forwarded_ip(
        self, send_message_mock
    ):
        """Aceita o IP original quando o request vem de proxy confiável."""
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {"phone_number_id": "123456789"},
                                "messages": [
                                    {
                                        "id": "wamid-allowlist-pass",
                                        "from": "5511999999999",
                                        "type": "text",
                                        "text": {"body": "menu"},
                                    }
                                ],
                            },
                        }
                    ]
                }
            ],
        }

        with patch("app.WEBHOOK_ALLOWED_IPS", "198.51.100.10"), patch(
            "app.TRUSTED_PROXY_IPS", "203.0.113.0/24"
        ):
            response = self.signed_post(
                payload,
                headers={"X-Forwarded-For": "198.51.100.10"},
                remote_addr="203.0.113.30",
            )

        self.assertEqual(response.status_code, 200)
        send_message_mock.assert_called_once()
        to_phone, payload = send_message_mock.call_args.args
        self.assertEqual(to_phone, "5511999999999")
        self.assert_initial_menu_payload(payload)

    @patch("app.send_whatsapp_message_payload")
    def test_post_webhook_ignores_spoofed_forwarded_ip_without_trusted_proxy(
        self, send_message_mock
    ):
        """Não deixa bypass da allowlist via `X-Forwarded-For` sem proxy confiável."""
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {"phone_number_id": "123456789"},
                                "messages": [
                                    {
                                        "id": "wamid-spoofed-xff",
                                        "from": "5511999999999",
                                        "type": "text",
                                        "text": {"body": "menu"},
                                    }
                                ],
                            },
                        }
                    ]
                }
            ],
        }

        with patch("app.WEBHOOK_ALLOWED_IPS", "198.51.100.10"), patch(
            "app.TRUSTED_PROXY_IPS", ""
        ):
            response = self.signed_post(
                payload,
                headers={"X-Forwarded-For": "198.51.100.10"},
                remote_addr="203.0.113.30",
            )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json["detail"], "forbidden source")
        send_message_mock.assert_not_called()

    @patch("app.send_whatsapp_message_payload")
    def test_post_webhook_rate_limits_requests(self, send_message_mock):
        """Bloqueia burst acima do limite configurado por IP."""
        first_payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {"phone_number_id": "123456789"},
                                "messages": [
                                    {
                                        "id": "wamid-rate-1",
                                        "from": "5511999999999",
                                        "type": "text",
                                        "text": {"body": "menu"},
                                    }
                                ],
                            },
                        }
                    ]
                }
            ],
        }
        second_payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {"phone_number_id": "123456789"},
                                "messages": [
                                    {
                                        "id": "wamid-rate-2",
                                        "from": "5511999999999",
                                        "type": "text",
                                        "text": {"body": "1"},
                                    }
                                ],
                            },
                        }
                    ]
                }
            ],
        }

        with patch("app.WEBHOOK_RATE_LIMIT_MAX_REQUESTS", 1), patch(
            "app.WEBHOOK_RATE_LIMIT_WINDOW_SECONDS", 60
        ):
            first_response = self.signed_post(first_payload)
            second_response = self.signed_post(second_payload)

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 429)
        self.assertEqual(send_message_mock.call_count, 1)

    @patch("app.send_whatsapp_message_payload")
    def test_post_webhook_acknowledges_send_failures_without_retrying_provider(
        self, send_message_mock
    ):
        """Mantém `200 OK` no webhook mesmo quando o envio para a Meta falha."""
        response_mock = Mock()
        response_mock.status_code = 400
        response_mock.json.return_value = {
            "error": {
                "code": 131030,
                "type": "OAuthException",
                "message": "Recipient phone number not in allowed list",
                "fbtrace_id": "trace-id",
            }
        }
        send_message_mock.side_effect = requests.HTTPError(response=response_mock)

        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {"phone_number_id": "123456789"},
                                "messages": [
                                    {
                                        "id": "wamid-error",
                                        "from": "5511999999999",
                                        "type": "text",
                                        "text": {"body": "2"},
                                    }
                                ],
                            },
                        }
                    ]
                }
            ],
        }

        response = self.signed_post(payload)

        self.assertEqual(response.status_code, 200)
        send_message_mock.assert_called_once()


# Permite executar os testes diretamente com `python test_app.py`.
if __name__ == "__main__":
    unittest.main()
