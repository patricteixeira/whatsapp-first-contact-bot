"""Aplicação Flask de bot de primeiro atendimento no WhatsApp."""

# Bibliotecas padrão usadas para assinatura, logs e controle simples em memória.
import hashlib
import hmac
import ipaddress
import logging
import os
import time
from collections import OrderedDict, deque
from threading import Lock
from typing import Any

# Dependências externas usadas no servidor HTTP e nas chamadas para a API da Meta.
import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from messages import (
    DEFAULT_MESSAGE,
    INITIAL_MENU_BUTTONS,
    INTERACTIVE_REPLY_OPTIONS,
    OTHER_MENU_BODY,
    OTHER_MENU_BUTTON_TEXT,
    OTHER_MENU_ROWS,
    PREDEFINED_MESSAGES,
    START_MENU_TRIGGERS,
    WELCOME_MESSAGE,
)


# Carrega variáveis definidas em `.env` antes de montar a configuração da aplicação.
load_dotenv()

# Cria a aplicação Flask e limita o tamanho máximo dos payloads aceitos.
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MAX_CONTENT_LENGTH", "1048576"))

# Configura o nível global de logs e cria o logger do módulo.
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

# Lê credenciais e parâmetros de execução a partir das variáveis de ambiente.
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "meu-token-de-verificacao")
APP_SECRET = os.getenv("META_APP_SECRET", "")
GRAPH_API_VERSION = os.getenv("WHATSAPP_GRAPH_API_VERSION", "v23.0")
MESSAGE_DEDUP_TTL_SECONDS = int(os.getenv("MESSAGE_DEDUP_TTL_SECONDS", "900"))
MESSAGE_DEDUP_MAX_ENTRIES = int(os.getenv("MESSAGE_DEDUP_MAX_ENTRIES", "5000"))
WAITRESS_THREADS = int(os.getenv("WAITRESS_THREADS", "4"))
WEBHOOK_RATE_LIMIT_WINDOW_SECONDS = int(
    os.getenv("WEBHOOK_RATE_LIMIT_WINDOW_SECONDS", "60")
)
WEBHOOK_RATE_LIMIT_MAX_REQUESTS = int(os.getenv("WEBHOOK_RATE_LIMIT_MAX_REQUESTS", "300"))
TRUSTED_PROXY_IPS = os.getenv("TRUSTED_PROXY_IPS", "")
WEBHOOK_ALLOWED_IPS = os.getenv("WEBHOOK_ALLOWED_IPS", "")
HUMAN_TAKEOVER_TOKEN = os.getenv("HUMAN_TAKEOVER_TOKEN", "")

# Guarda `message.id` já processados para reduzir resposta duplicada.
processed_message_ids: OrderedDict[str, float] = OrderedDict()
processed_message_ids_lock = Lock()

# Guarda timestamps recentes por IP para limitar bursts no webhook.
webhook_request_timestamps: dict[str, deque[float]] = {}
webhook_request_timestamps_lock = Lock()

# Guarda os telefones que estao sob atendimento humano manual no momento.
human_takeover_numbers: set[str] = set()
human_takeover_numbers_lock = Lock()


def mask_value(value: str | None, visible_digits: int = 4) -> str:
    """Mascara números em logs, preservando apenas os últimos dígitos."""
    # Extrai somente dígitos para não expor o valor bruto recebido.
    digits = "".join(char for char in value or "" if char.isdigit())
    if len(digits) <= visible_digits:
        return "***"
    return f"***{digits[-visible_digits:]}"


def normalize_text(text: str) -> str:
    """Normaliza a entrada do usuário para comparação direta."""
    return (text or "").strip().lower()


def normalize_phone_number(phone: str | None) -> str | None:
    """Normaliza telefones para comparacao e envio, preservando apenas digitos."""
    digits = "".join(char for char in phone or "" if char.isdigit())
    return digits or None


def is_start_menu_request(user_text: str | None) -> bool:
    """Confere se o texto deve abrir o menu inicial clicavel."""
    return normalize_text(user_text or "") in START_MENU_TRIGGERS


def has_valid_human_takeover_token(provided_token: str | None) -> bool:
    """Valida o token simples usado para controlar o takeover manual."""
    if not HUMAN_TAKEOVER_TOKEN or not provided_token:
        return False
    return hmac.compare_digest(HUMAN_TAKEOVER_TOKEN, provided_token)


def get_takeover_phone_from_request() -> str | None:
    """Extrai o telefone alvo do endpoint de takeover manual."""
    if request.method == "GET":
        raw_phone = request.args.get("phone")
    else:
        payload = request.get_json(silent=True) or {}
        raw_phone = payload.get("phone") if isinstance(payload, dict) else None

    return normalize_phone_number(raw_phone)


def set_human_takeover(phone: str) -> None:
    """Ativa a pausa do bot para um numero especifico."""
    with human_takeover_numbers_lock:
        human_takeover_numbers.add(phone)


def clear_human_takeover(phone: str) -> None:
    """Remove a pausa manual do bot para um numero especifico."""
    with human_takeover_numbers_lock:
        human_takeover_numbers.discard(phone)


def is_human_takeover_active(phone: str) -> bool:
    """Informa se um numero esta temporariamente sob atendimento humano."""
    with human_takeover_numbers_lock:
        return phone in human_takeover_numbers


def reset_human_takeovers() -> None:
    """Limpa o takeover manual em memoria; util para testes automatizados."""
    with human_takeover_numbers_lock:
        human_takeover_numbers.clear()


def validate_runtime_configuration(debug: bool) -> None:
    """Falha cedo quando a configuração essencial do runtime está inconsistente."""
    if debug:
        return

    missing_variables = []
    if not VERIFY_TOKEN:
        missing_variables.append("WHATSAPP_VERIFY_TOKEN")
    if not APP_SECRET:
        missing_variables.append("META_APP_SECRET")

    if missing_variables:
        missing_variables_text = ", ".join(missing_variables)
        raise RuntimeError(
            f"Variaveis obrigatorias ausentes para executar o webhook com seguranca: {missing_variables_text}"
        )

    parse_ip_networks(TRUSTED_PROXY_IPS, "TRUSTED_PROXY_IPS")
    parse_ip_networks(WEBHOOK_ALLOWED_IPS, "WEBHOOK_ALLOWED_IPS")


def parse_ip_networks(raw_value: str, variable_name: str):
    """Converte uma lista de IPs/CIDRs em redes válidas para comparação."""
    networks = []
    for raw_candidate in (raw_value or "").split(","):
        candidate = raw_candidate.strip()
        if not candidate:
            continue

        try:
            networks.append(ipaddress.ip_network(candidate, strict=False))
        except ValueError as exc:
            raise RuntimeError(
                f"Valor invalido em {variable_name}: {candidate}"
            ) from exc

    return tuple(networks)


def normalize_ip_address(ip_text: str | None) -> str | None:
    """Normaliza IPs válidos para representação canônica."""
    if not ip_text:
        return None

    try:
        return str(ipaddress.ip_address(ip_text.strip()))
    except ValueError:
        return None


def is_ip_in_networks(ip_text: str | None, networks) -> bool:
    """Confere se um IP pertence a uma lista de redes válidas."""
    normalized_ip = normalize_ip_address(ip_text)
    if normalized_ip is None:
        return False

    ip_object = ipaddress.ip_address(normalized_ip)
    return any(ip_object in network for network in networks)


def get_request_forwarded_for_chain() -> list[str]:
    """Extrai a cadeia de IPs válidos presente em `X-Forwarded-For`."""
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    forwarded_chain = []

    for raw_candidate in forwarded_for.split(","):
        normalized_ip = normalize_ip_address(raw_candidate)
        if normalized_ip is not None:
            forwarded_chain.append(normalized_ip)

    return forwarded_chain


def get_request_client_ip() -> str:
    """Resolve o IP do cliente sem confiar em proxy não declarado."""
    remote_ip = normalize_ip_address(request.remote_addr)
    if remote_ip is None:
        return request.remote_addr or "unknown"

    trusted_proxy_networks = parse_ip_networks(TRUSTED_PROXY_IPS, "TRUSTED_PROXY_IPS")
    if not is_ip_in_networks(remote_ip, trusted_proxy_networks):
        return remote_ip

    ip_chain = get_request_forwarded_for_chain() + [remote_ip]
    while ip_chain and is_ip_in_networks(ip_chain[-1], trusted_proxy_networks):
        ip_chain.pop()

    if not ip_chain:
        return remote_ip

    return ip_chain[-1]


def is_request_source_allowed(client_ip: str) -> bool:
    """Confere a allowlist opcional de origem do webhook."""
    allowed_networks = parse_ip_networks(WEBHOOK_ALLOWED_IPS, "WEBHOOK_ALLOWED_IPS")
    if not allowed_networks:
        return True

    return is_ip_in_networks(client_ip, allowed_networks)


def is_valid_meta_signature(raw_body: bytes, signature_header: str | None) -> bool:
    """Valida a assinatura HMAC enviada pela Meta no webhook."""
    # Sem segredo ou sem assinatura não há como confiar no evento.
    if not APP_SECRET or not signature_header:
        return False

    # Recalcula a assinatura esperada com o mesmo corpo bruto recebido.
    expected_signature = "sha256=" + hmac.new(
        APP_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected_signature, signature_header)


def build_meta_error_summary(response: requests.Response | None) -> dict[str, Any]:
    """Resume o erro retornado pela Meta sem despejar a resposta inteira no log."""
    if response is None:
        return {"status": None, "code": None, "type": None, "message": "no response"}

    # Tenta extrair o bloco `error` padronizado da Graph API.
    try:
        error = response.json().get("error", {})
    except ValueError:
        return {
            "status": response.status_code,
            "code": None,
            "type": None,
            "message": "non-json response",
        }

    return {
        "status": response.status_code,
        "code": error.get("code"),
        "type": error.get("type"),
        "message": error.get("message"),
        "fbtrace_id": error.get("fbtrace_id"),
    }


def summarize_inbound_event(payload: dict[str, Any]) -> dict[str, Any]:
    """Gera um resumo seguro do evento recebido para observabilidade."""
    entries = payload.get("entry")
    summary: dict[str, Any] = {
        "object": payload.get("object"),
        "entry_count": len(entries) if isinstance(entries, list) else 0,
        "change_count": 0,
        "message_count": 0,
        "text_message_count": 0,
        "interactive_message_count": 0,
        "status_count": 0,
    }
    fields: set[str] = set()
    phone_number_ids: set[str] = set()
    message_ids: list[str] = []

    for change, value in iter_event_changes(payload):
        summary["change_count"] += 1

        field = change.get("field")
        if isinstance(field, str):
            fields.add(field)

        metadata = value.get("metadata")
        if isinstance(metadata, dict):
            phone_number_id = metadata.get("phone_number_id")
            if phone_number_id:
                phone_number_ids.add(mask_value(phone_number_id))

        messages = value.get("messages")
        if isinstance(messages, list):
            summary["message_count"] += len(messages)
            for message in messages:
                if not isinstance(message, dict):
                    continue
                if message.get("type") == "text":
                    summary["text_message_count"] += 1
                if message.get("type") == "interactive":
                    summary["interactive_message_count"] += 1
                message_id = message.get("id")
                if isinstance(message_id, str) and len(message_ids) < 3:
                    message_ids.append(message_id)

        statuses = value.get("statuses")
        if isinstance(statuses, list):
            summary["status_count"] += len(statuses)

    if fields:
        summary["fields"] = sorted(fields)
    if phone_number_ids:
        summary["phone_number_ids"] = sorted(phone_number_ids)
    if message_ids:
        summary["message_ids"] = message_ids
    if payload.get("entry") is not None and not isinstance(entries, list):
        summary["malformed"] = True
    return summary


def iter_event_changes(payload: dict[str, Any]):
    """Itera por todas as `changes` válidas presentes no payload recebido."""
    entries = payload.get("entry")
    if not isinstance(entries, list):
        return

    for entry in entries:
        if not isinstance(entry, dict):
            continue

        changes = entry.get("changes")
        if not isinstance(changes, list):
            continue

        for change in changes:
            if not isinstance(change, dict):
                continue

            value = change.get("value")
            if not isinstance(value, dict):
                value = {}
            yield change, value


def get_interactive_reply(message: dict[str, Any]) -> tuple[str, str | None] | None:
    """Extrai o ID de respostas interativas da Meta."""
    interactive_payload = message.get("interactive")
    if not isinstance(interactive_payload, dict):
        return None

    button_reply = interactive_payload.get("button_reply")
    if isinstance(button_reply, dict):
        return "button_reply", button_reply.get("id")

    list_reply = interactive_payload.get("list_reply")
    if isinstance(list_reply, dict):
        return "list_reply", list_reply.get("id")

    return None


def iter_customer_messages(payload: dict[str, Any]):
    """Itera mensagens de texto e respostas interativas do cliente."""
    for change, value in iter_event_changes(payload):
        metadata = value.get("metadata")
        event_phone_number_id = None
        if isinstance(metadata, dict):
            event_phone_number_id = metadata.get("phone_number_id")

        messages = value.get("messages")
        if not isinstance(messages, list):
            continue

        for message in messages:
            if not isinstance(message, dict):
                continue

            message_type = message.get("type")
            base_message = {
                "field": change.get("field"),
                "event_phone_number_id": event_phone_number_id,
                "message_id": message.get("id"),
                "from_phone": message.get("from"),
                "body": None,
                "reply_id": None,
            }

            if message_type == "text":
                text_payload = message.get("text")
                body = None
                if isinstance(text_payload, dict):
                    body = text_payload.get("body")

                yield {**base_message, "message_kind": "text", "body": body}
                continue

            if message_type == "interactive":
                interactive_reply = get_interactive_reply(message)
                if interactive_reply is None:
                    continue

                reply_kind, reply_id = interactive_reply
                yield {
                    **base_message,
                    "message_kind": reply_kind,
                    "reply_id": reply_id,
                }


def iter_text_messages(payload: dict[str, Any]):
    """Itera por todas as mensagens de texto presentes no payload do webhook."""
    for customer_message in iter_customer_messages(payload):
        if customer_message["message_kind"] == "text":
            yield customer_message


def cleanup_processed_message_ids(now: float | None = None) -> None:
    """Remove entradas expiradas ou acima do limite máximo da deduplicação."""
    current_time = now if now is not None else time.time()
    expiration_time = current_time - MESSAGE_DEDUP_TTL_SECONDS

    # Elimina itens antigos do início do cache até que ele volte ao tamanho válido.
    while processed_message_ids:
        oldest_message_id, oldest_seen_at = next(iter(processed_message_ids.items()))
        if (
            oldest_seen_at >= expiration_time
            and len(processed_message_ids) <= MESSAGE_DEDUP_MAX_ENTRIES
        ):
            break
        processed_message_ids.pop(oldest_message_id, None)


def mark_message_processed(message_id: str, now: float | None = None) -> bool:
    """Marca uma mensagem como processada e informa se ela era inédita."""
    with processed_message_ids_lock:
        # Limpa o cache antes da consulta para não segurar item já vencido.
        cleanup_processed_message_ids(now=now)
        if message_id in processed_message_ids:
            return False

        # Registra o horário atual para futuras limpezas por TTL.
        processed_message_ids[message_id] = now if now is not None else time.time()
        cleanup_processed_message_ids(now=now)
        return True


def reset_processed_message_ids() -> None:
    """Limpa a memória de deduplicação; útil para testes automatizados."""
    with processed_message_ids_lock:
        processed_message_ids.clear()


def cleanup_webhook_rate_limits(
    client_ip: str | None = None, now: float | None = None
) -> None:
    """Remove timestamps expirados do rate limit do webhook."""
    current_time = now if now is not None else time.time()
    expiration_time = current_time - WEBHOOK_RATE_LIMIT_WINDOW_SECONDS
    client_ips = [client_ip] if client_ip else list(webhook_request_timestamps.keys())

    for ip in client_ips:
        timestamps = webhook_request_timestamps.get(ip)
        if timestamps is None:
            continue

        while timestamps and timestamps[0] <= expiration_time:
            timestamps.popleft()

        if not timestamps:
            webhook_request_timestamps.pop(ip, None)


def is_webhook_rate_limited(client_ip: str, now: float | None = None) -> bool:
    """Aplica um rate limit simples por IP no endpoint do webhook."""
    if WEBHOOK_RATE_LIMIT_MAX_REQUESTS <= 0:
        return False

    current_time = now if now is not None else time.time()
    with webhook_request_timestamps_lock:
        cleanup_webhook_rate_limits(client_ip=client_ip, now=current_time)
        timestamps = webhook_request_timestamps.setdefault(client_ip, deque())
        if len(timestamps) >= WEBHOOK_RATE_LIMIT_MAX_REQUESTS:
            return True

        timestamps.append(current_time)
        return False


def reset_webhook_rate_limits() -> None:
    """Limpa a memória do rate limit; útil para testes automatizados."""
    with webhook_request_timestamps_lock:
        webhook_request_timestamps.clear()


def has_supported_json_content_type() -> bool:
    """Confere se o POST do webhook chegou com `application/json`."""
    return request.mimetype == "application/json"


def send_whatsapp_message_payload(to_phone: str, message_payload: dict[str, Any]) -> None:
    """Envia um payload de mensagem para a Cloud API do WhatsApp."""
    # Sem token e sem `phone_number_id`, o bot não consegue enviar mensagens.
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        raise RuntimeError(
            "Defina WHATSAPP_TOKEN e WHATSAPP_PHONE_NUMBER_ID nas variáveis de ambiente."
        )

    # Monta a URL do endpoint oficial de envio da Graph API.
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {"messaging_product": "whatsapp", "to": to_phone, **message_payload}

    # Envia a requisição HTTP síncrona e falha se a Meta retornar erro.
    response = requests.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()

    # Tenta registrar o `message.id` retornado pela Meta para rastreabilidade.
    response_message_id = None
    try:
        response_json = response.json()
        if response_json.get("messages"):
            response_message_id = response_json["messages"][0].get("id")
    except ValueError:
        response_message_id = None

    logger.info(
        "Mensagem enviada com sucesso. destino=%s response_message_id=%s",
        mask_value(to_phone),
        response_message_id,
    )


def send_whatsapp_text_message(to_phone: str, message_text: str) -> None:
    """Envia uma mensagem de texto para a Cloud API do WhatsApp."""
    send_whatsapp_message_payload(
        to_phone,
        {"type": "text", "text": {"body": message_text}},
    )


def send_whatsapp_button_message(
    to_phone: str, body_text: str, buttons: list[dict[str, str]]
) -> None:
    """Envia uma mensagem interativa com botoes de resposta."""
    send_whatsapp_message_payload(
        to_phone,
        {
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body_text},
                "action": {
                    "buttons": [
                        {
                            "type": "reply",
                            "reply": {
                                "id": button["id"],
                                "title": button["title"],
                            },
                        }
                        for button in buttons
                    ]
                },
            },
        },
    )


def send_whatsapp_list_message(
    to_phone: str,
    body_text: str,
    button_text: str,
    rows: list[dict[str, str]],
    section_title: str = "Outros assuntos",
) -> None:
    """Envia uma mensagem interativa do tipo lista."""
    formatted_rows = []
    for row in rows:
        formatted_row = {"id": row["id"], "title": row["title"]}
        if row.get("description"):
            formatted_row["description"] = row["description"]
        formatted_rows.append(formatted_row)

    send_whatsapp_message_payload(
        to_phone,
        {
            "type": "interactive",
            "interactive": {
                "type": "list",
                "body": {"text": body_text},
                "action": {
                    "button": button_text,
                    "sections": [
                        {
                            "title": section_title,
                            "rows": formatted_rows,
                        }
                    ],
                },
            },
        },
    )


def send_initial_menu_message(to_phone: str) -> None:
    """Abre o menu principal com 3 opcoes clicaveis."""
    send_whatsapp_button_message(to_phone, WELCOME_MESSAGE, INITIAL_MENU_BUTTONS)


def send_other_options_message(to_phone: str) -> None:
    """Abre a lista com as demais opcoes do menu."""
    send_whatsapp_list_message(
        to_phone,
        OTHER_MENU_BODY,
        OTHER_MENU_BUTTON_TEXT,
        OTHER_MENU_ROWS,
    )


def send_reply_for_customer_message(
    to_phone: str, customer_message: dict[str, Any]
) -> None:
    """Roteia texto inicial, botoes e listas para a resposta correta."""
    message_kind = customer_message["message_kind"]

    if message_kind == "text":
        if is_start_menu_request(customer_message.get("body")):
            send_initial_menu_message(to_phone)
            return

        send_whatsapp_text_message(to_phone, DEFAULT_MESSAGE)
        return

    reply_id = customer_message.get("reply_id")
    if message_kind == "button_reply" and reply_id == "outros":
        send_other_options_message(to_phone)
        return

    menu_key = INTERACTIVE_REPLY_OPTIONS.get(reply_id)
    reply_text = PREDEFINED_MESSAGES.get(menu_key, DEFAULT_MESSAGE)
    send_whatsapp_text_message(to_phone, reply_text)


@app.get("/")
def healthcheck():
    """Endpoint simples para monitoramento básico da aplicação."""
    return jsonify({"status": "ok", "service": "whatsapp-bot"})


@app.route("/human-takeover", methods=["GET", "POST", "DELETE"])
def human_takeover():
    """Consulta e altera a pausa manual da automacao por numero do paciente."""
    if not HUMAN_TAKEOVER_TOKEN:
        logger.error("Endpoint de takeover chamado sem HUMAN_TAKEOVER_TOKEN configurado.")
        return jsonify({"status": "error", "detail": "human takeover not configured"}), 503

    provided_token = request.headers.get("X-Admin-Token")
    if not has_valid_human_takeover_token(provided_token):
        logger.warning("Tentativa de takeover rejeitada por token administrativo invalido.")
        return jsonify({"status": "error", "detail": "invalid admin token"}), 403

    phone = get_takeover_phone_from_request()
    if not phone:
        return jsonify({"status": "error", "detail": "phone is required"}), 400

    if request.method == "GET":
        return jsonify(
            {
                "status": "ok",
                "phone": phone,
                "human_takeover_active": is_human_takeover_active(phone),
            }
        )

    if request.method == "POST":
        # Marca o contato para que o webhook deixe de responder automaticamente.
        set_human_takeover(phone)
        logger.info("Takeover humano ativado. phone=%s", mask_value(phone))
        return jsonify({"status": "ok", "phone": phone, "human_takeover_active": True})

    # Libera o contato e permite que o bot volte a responder nas proximas mensagens.
    clear_human_takeover(phone)
    logger.info("Takeover humano desativado. phone=%s", mask_value(phone))
    return jsonify({"status": "ok", "phone": phone, "human_takeover_active": False})


@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    """Endpoint usado pela Meta para validar o webhook e entregar eventos."""
    if request.method == "GET":
        # Na validação inicial a Meta envia os parâmetros `hub.*` pela query string.
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")

        logger.info(
            "Webhook GET recebido. mode=%s challenge_present=%s",
            mode,
            bool(challenge),
        )

        # Só devolve o challenge quando o token de verificação bate.
        if mode == "subscribe" and token == VERIFY_TOKEN:
            return challenge or "", 200

        logger.warning("Falha na verificacao do webhook.")
        return "Token invalido", 403

    if not has_supported_json_content_type():
        logger.warning("Webhook rejeitado por content-type nao suportado. content_type=%s", request.content_type)
        return jsonify({"status": "error", "detail": "unsupported media type"}), 415

    client_ip = get_request_client_ip()
    if not is_request_source_allowed(client_ip):
        logger.warning("Webhook rejeitado por origem nao permitida. client_ip=%s", client_ip)
        return jsonify({"status": "error", "detail": "forbidden source"}), 403

    if is_webhook_rate_limited(client_ip):
        logger.warning("Webhook bloqueado por rate limit. client_ip=%s", client_ip)
        return jsonify({"status": "error", "detail": "rate limit exceeded"}), 429

    # No POST, o corpo bruto é necessário para validar a assinatura HMAC.
    raw_body = request.get_data(cache=True)
    signature_header = request.headers.get("X-Hub-Signature-256")

    if not is_valid_meta_signature(raw_body, signature_header):
        logger.warning("Webhook rejeitado por assinatura invalida.")
        return jsonify({"status": "error", "detail": "invalid signature"}), 403

    # Desserializa o JSON apenas depois de validar a origem do evento.
    payload = request.get_json(silent=True) or {}
    logger.info("Evento recebido da Meta. resumo=%s", summarize_inbound_event(payload))

    # Ignora objetos que não pertencem ao fluxo do WhatsApp Business Platform.
    if payload.get("object") != "whatsapp_business_account":
        logger.warning("Objeto inesperado recebido no webhook.")
        return "ok", 200

    customer_messages = list(iter_customer_messages(payload))
    if not customer_messages:
        logger.info("Evento recebido sem mensagem do cliente processavel. Ignorando.")
        return "ok", 200

    for customer_message in customer_messages:
        event_phone_number_id = customer_message["event_phone_number_id"]
        if PHONE_NUMBER_ID and event_phone_number_id and event_phone_number_id != PHONE_NUMBER_ID:
            logger.warning(
                "Mensagem ignorada por phone_number_id nao configurado. configurado=%s recebido=%s message_id=%s",
                mask_value(PHONE_NUMBER_ID),
                mask_value(event_phone_number_id),
                customer_message["message_id"],
            )
            continue

        message_id = customer_message["message_id"]
        from_phone = customer_message["from_phone"]
        normalized_from_phone = normalize_phone_number(from_phone)

        if not message_id or not normalized_from_phone:
            logger.info(
                "Mensagem recebida com campos incompletos. field=%s",
                customer_message["field"],
            )
            continue

        if customer_message["message_kind"] == "text" and not customer_message["body"]:
            logger.info(
                "Mensagem de texto recebida sem corpo. field=%s",
                customer_message["field"],
            )
            continue

        if customer_message["message_kind"] != "text" and not customer_message["reply_id"]:
            logger.info(
                "Mensagem interativa recebida sem ID. field=%s",
                customer_message["field"],
            )
            continue

        # Evita processar o mesmo `message.id` mais de uma vez.
        if not mark_message_processed(message_id):
            logger.info("Mensagem duplicada ignorada. message_id=%s", message_id)
            continue

        # Quando o humano assume o contato, o bot fica silencioso ate a liberacao manual.
        if is_human_takeover_active(normalized_from_phone):
            logger.info(
                "Resposta automatica suprimida por takeover humano. from_phone=%s message_id=%s",
                mask_value(normalized_from_phone),
                message_id,
            )
            continue

        try:
            send_reply_for_customer_message(normalized_from_phone, customer_message)
        except RuntimeError as exc:
            logger.error("Configuracao incompleta do bot: %s", exc)
        except requests.HTTPError as exc:
            logger.error(
                "Erro HTTP ao enviar mensagem para a API do WhatsApp. resumo=%s",
                build_meta_error_summary(exc.response),
            )
        except requests.RequestException:
            logger.exception("Falha de rede ao enviar mensagem para a API do WhatsApp.")

    # O webhook responde `200` para a Meta mesmo quando o envio falha internamente.
    return "ok", 200


def run() -> None:
    """Sobe o app em modo debug com Flask ou em produção com Waitress."""
    port = int(os.getenv("PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "").lower() in {"1", "true", "yes"}
    validate_runtime_configuration(debug)

    # Em desenvolvimento usa o servidor embutido do Flask.
    if debug:
        app.run(host="0.0.0.0", port=port, debug=True)
        return

    # Em modo normal usa o Waitress para servir o WSGI em produção.
    from waitress import serve

    serve(app, host="0.0.0.0", port=port, threads=WAITRESS_THREADS)


# Permite executar o arquivo diretamente com `python app.py`.
if __name__ == "__main__":
    run()
