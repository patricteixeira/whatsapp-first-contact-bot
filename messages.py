"""Catálogo das mensagens automáticas do bot.

Este catálogo é um exemplo fictício de uma clínica (Clínica Aurora).
Adapte os textos, links e opções para a sua operação.
"""

# Mensagem principal mostrada quando o cliente pede o menu inicial.
WELCOME_MESSAGE = (
    "Olá! Seja bem-vindo à Clínica Aurora. Sou a assistente virtual da clínica.\n\n"
    "Para agilizarmos seu atendimento, escolha uma opção abaixo."
)

# Mensagem padrão usada quando o texto recebido não bate com nenhum gatilho conhecido.
DEFAULT_MESSAGE = (
    "Agradecemos seu contato!\n\n"
    "Para te ajudarmos melhor, envie a palavra *menu* para reabrir as opções de atendimento, "
    "ou descreva sua dúvida com mais detalhes para que nossa equipe possa retornar."
)

START_MENU_TRIGGERS = {"oi", "bom dia", "boa tarde", "boa noite", "ola", "olá", "menu"}

INITIAL_MENU_BUTTONS = [
    {"id": "opcao_1", "title": "Primeira Consulta"},
    {"id": "opcao_2", "title": "Agendar Retorno"},
    {"id": "outros", "title": "Outros assuntos"},
]

OTHER_MENU_BODY = "Escolha o assunto para continuar:"
OTHER_MENU_BUTTON_TEXT = "Ver opções"

OTHER_MENU_ROWS = [
    {
        "id": "menu_item_3",
        "title": "Material de boas-vindas",
        "description": "Guias e orientações iniciais",
    },
    {
        "id": "menu_item_4",
        "title": "Dúvida sobre medicação",
        "description": "Horários, dose ou esquecimento",
    },
    {
        "id": "menu_item_5",
        "title": "Efeito colateral",
        "description": "Dúvidas sobre reações adversas",
    },
    {
        "id": "menu_item_6",
        "title": "Receita/relatórios",
        "description": "Segunda via e documentos",
    },
    {
        "id": "menu_item_7",
        "title": "Onde comprar",
        "description": "Auxílio com aquisição do medicamento",
    },
    {
        "id": "menu_item_8",
        "title": "Financeiro/NF",
        "description": "Pagamentos e notas fiscais",
    },
    {
        "id": "menu_item_9",
        "title": "Dúvidas clínicas",
        "description": "Sintomas ou outras condições",
    },
    {
        "id": "menu_item_0",
        "title": "Falar com atendente",
        "description": "Outros assuntos",
    },
]

INTERACTIVE_REPLY_OPTIONS = {
    "opcao_1": "1",
    "opcao_2": "2",
    "menu_item_3": "3",
    "menu_item_4": "4",
    "menu_item_5": "5",
    "menu_item_6": "6",
    "menu_item_7": "7",
    "menu_item_8": "8",
    "menu_item_9": "9",
    "menu_item_0": "0",
}

# Catálogo de respostas automáticas reaproveitado pelas opções clicáveis.
PREDEFINED_MESSAGES: dict[str, str] = {
    # Saudações e atalhos que levam ao menu principal.
    "oi": WELCOME_MESSAGE,
    "bom dia": WELCOME_MESSAGE,
    "boa tarde": WELCOME_MESSAGE,
    "boa noite": WELCOME_MESSAGE,
    "ola": WELCOME_MESSAGE,
    "olá": WELCOME_MESSAGE,
    "menu": WELCOME_MESSAGE,
    # Opção 1: agendamento da primeira consulta.
    "1": (
        "Que bom que decidiu iniciar seu tratamento.\n"
        "Escolha o melhor horário pelo link: https://exemplo.com/agendar-primeira-consulta\n\n"
        "Após o agendamento, você receberá um formulário de pré-consulta.\n"
        "Agradecemos a confiança."
    ),
    # Opção 2: agendamento de retorno.
    "2": (
        "Hora de avaliarmos sua evolução.\n"
        "Agende seu retorno aqui: https://exemplo.com/agendar-retorno\n\n"
        "Lembrete: para que a consulta seja mais produtiva, organize previamente as informações dos últimos dias.\n"
        "Você receberá um formulário pré-consulta. Até breve!"
    ),
    # Opção 3: material de boas-vindas.
    "3": (
        "Aqui estão seus documentos de apoio:\n\n"
        "Guia do paciente: https://exemplo.com/guia-paciente\n"
        "Diário de evolução: https://exemplo.com/diario-evolucao\n\n"
        "Recomendamos salvar esses links nos seus favoritos."
    ),
    # Opção 4: triagem de dúvida sobre medicação.
    "4": (
        "Para te ajudarmos com a medicação, por favor responda:\n\n"
        "Qual medicamento você está usando no momento?\n"
        "Qual a dose e em quais horários?\n"
        "Qual foi a última alteração de dose?\n\n"
        "Aguarde um instante que nossa equipe analisará seu histórico para te orientar."
    ),
    # Opção 5: orientação inicial para efeitos colaterais.
    "5": (
        "Sentimos pelo desconforto.\n"
        "Geralmente os efeitos são leves e passageiros.\n\n"
        "Se o sintoma for intenso ou persistente, descreva-o aqui em detalhes e nossa equipe vai te orientar."
    ),
    # Opção 6: solicitação de documentos.
    "6": (
        "Para solicitações de documentos, informe:\n\n"
        "Nome completo do paciente;\n"
        "Qual documento precisa (receita, laudo ou relatório);\n"
        "Para qual finalidade (compra, convênio ou viagem).\n\n"
        "Prazo de emissão de novos documentos: até 3 dias úteis."
    ),
    # Opção 7: suporte para compra do medicamento.
    "7": (
        "O processo de aquisição pode gerar dúvidas no início.\n\n"
        "Nos informe qual medicamento foi prescrito e nossa equipe envia as opções de farmácia parceira.\n"
        "Se precisar do PDF da receita, nos avise."
    ),
    # Opção 8: atendimento financeiro.
    "8": (
        "Para questões financeiras ou emissão de nota fiscal para reembolso, envie:\n\n"
        "Nome do titular do pagamento;\n"
        "CPF do titular;\n"
        "Data da consulta;\n"
        "Endereço completo;\n"
        "E-mail para envio da NF.\n\n"
        "Nossa equipe administrativa processará seu pedido em breve."
    ),
    # Opção 9: triagem de dúvidas clínicas fora do menu.
    "9": (
        "Para dúvidas clínicas não listadas, descreva com o máximo de detalhes:\n\n"
        "Quais sintomas surgiram?\n"
        "Quando começaram?\n"
        "Você está usando qual medicação e em qual dose?\n"
        "Houve alguma mudança recente no tratamento?\n\n"
        "Nossa equipe analisará e orientará você assim que possível."
    ),
    # Opção 0: encaminhamento para atendimento humano.
    "0": (
        "Sua mensagem foi encaminhada para nossa fila de atendimento humano.\n\n"
        "Por favor, deixe sua dúvida escrita da forma mais clara possível para que possamos te ajudar com agilidade assim que estivermos disponíveis."
    ),
}
