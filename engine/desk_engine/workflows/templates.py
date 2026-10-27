"""Ready-made workflows for common finance routines. Each one is saved as a draft (switched off) so the person reviews
the prompts, picks files or connections, and switches it on. Only tools that exist are referenced; agent steps do the
work that depends on the workspace's own systems."""

from __future__ import annotations

from typing import Any

TEMPLATES: list[dict[str, Any]] = [
    {
        "key": "month_end_pack",
        "name": "Month-end pack",
        "description": "On the 2nd of each month: build the actual-vs-budget workbook for last month, have the reviewer "
                       "check it, ask a controller to approve, then tell the team where it is.",
        "category": "Close",
        "definition": {
            "trigger": {"type": "schedule", "cron": "0 7 2 * *"},
            "steps": [
                {"id": "pack", "type": "agent", "name": "Build the pack", "prompt":
                    "Build last month's management pack as a workbook: profit and loss actual against budget by "
                    "account, variance and variance %, with a short commentary on the five largest variances. Use the "
                    "accounting system or the imported datasets; every figure must come from a query. Today is "
                    "{{today}}."},
                {"id": "approve", "type": "approval", "name": "Controller approval", "message":
                    "Check the month-end pack and its open review findings before it is shared."},
                {"id": "tell", "type": "notify", "name": "Tell the team", "recipients": "editors",
                 "channels": ["in_app", "email"], "message":
                    "The month-end pack for {{workspace.name}} is approved.\n\n{{steps.pack.output}}"},
            ],
        },
    },
    {
        "key": "weekly_ar",
        "name": "Weekly receivables follow-up",
        "description": "Every Monday: list overdue customer invoices by age, draft polite reminder e-mails for the "
                       "largest ones (drafts only, nothing is sent), and post the summary.",
        "category": "Receivables",
        "definition": {
            "trigger": {"type": "schedule", "cron": "30 7 * * 1"},
            "steps": [
                {"id": "aging", "type": "agent", "name": "Overdue invoices", "prompt":
                    "List customer invoices that are overdue as of {{today}}, grouped by customer with 1–30, 31–60, "
                    "61–90 and over 90 days buckets and the total per bucket. Then create Gmail drafts (do not send) "
                    "reminding the five largest overdue customers, quoting invoice numbers, amounts and due dates."},
                {"id": "any", "type": "condition", "name": "Anything overdue?", "left": "{{steps.aging.output}}",
                 "op": "not_empty", "right": ""},
                {"id": "post", "type": "notify", "name": "Post the summary", "recipients": "editors",
                 "channels": ["in_app"], "message": "Receivables this week:\n{{steps.aging.output}}"},
            ],
        },
    },
    {
        "key": "ap_due",
        "name": "Supplier bills due this week",
        "description": "Every Thursday: list supplier bills due in the next seven days with the cash needed, and ask "
                       "for approval of the payment run list.",
        "category": "Payables",
        "definition": {
            "trigger": {"type": "schedule", "cron": "0 8 * * 4"},
            "steps": [
                {"id": "due", "type": "agent", "name": "Bills due", "prompt":
                    "List supplier bills due between {{today}} and seven days later: supplier, bill number, due date, "
                    "amount, and the total cash needed. Flag any bill already overdue and any supplier with more than "
                    "one bill due."},
                {"id": "approve", "type": "approval", "name": "Approve payment list", "message":
                    "Approve the list of bills to pay this week."},
                {"id": "tell", "type": "notify", "name": "Confirm", "recipients": "me", "channels": ["in_app"],
                 "message": "Payment list approved:\n{{steps.due.output}}"},
            ],
        },
    },
    {
        "key": "bank_statement_mail",
        "name": "Bank statements from e-mail",
        "description": "When the bank e-mails a statement: save the attachment to the documents and summarise opening "
                       "balance, closing balance and unusual items.",
        "category": "Cash",
        "definition": {
            "trigger": {"type": "email", "query": "from:(bank) subject:(statement) has:attachment"},
            "steps": [
                {"id": "read", "type": "agent", "name": "Read the statement", "prompt":
                    "A bank statement arrived: “{{trigger.subject}}” from {{trigger.from}} (message "
                    "{{trigger.message_id}}). Save its attachment to the documents, then report the opening balance, "
                    "the closing balance, the number of transactions and any single transaction above 10,000 or with "
                    "an unfamiliar counterparty. Cite the statement."},
                {"id": "tell", "type": "notify", "name": "Share", "recipients": "editors", "channels": ["in_app"],
                 "message": "{{steps.read.output}}"},
            ],
        },
    },
    {
        "key": "erp_webhook",
        "name": "Event from another system",
        "description": "Another system (an ERP, a payment provider, a form) posts an event; the assistant checks it "
                       "and asks for approval when something needs a person.",
        "category": "Integrations",
        "definition": {
            "trigger": {"type": "webhook"},
            "steps": [
                {"id": "check", "type": "agent", "name": "Check the event", "prompt":
                    "This event arrived from another system:\n{{trigger.text}}\n\nExplain in three sentences what it "
                    "is and whether anything needs a person's decision (amounts above the approval limit, new "
                    "suppliers, changed bank details). Start the answer with ACTION if a decision is needed."},
                {"id": "needs", "type": "condition", "name": "Needs a decision?", "left": "{{steps.check.output}}",
                 "op": "contains", "right": "ACTION"},
                {"id": "approve", "type": "approval", "name": "Decide", "message": "{{steps.check.output}}"},
            ],
        },
    },
    {
        "key": "refresh_file",
        "name": "Refresh a workbook every morning",
        "description": "Re-run the queries behind an evidence-linked workbook each working day and tell you when the "
                       "new version is ready. Choose the workbook before switching it on.",
        "category": "Files",
        "definition": {
            "trigger": {"type": "schedule", "cron": "0 6 * * 1-5"},
            "steps": [
                {"id": "refresh", "type": "file", "name": "Refresh the workbook", "action": "refresh", "file_id": "",
                 "retries": 2, "retry_delay": 60},
                {"id": "tell", "type": "notify", "name": "Tell me", "recipients": "me", "channels": ["in_app"],
                 "message": "{{steps.refresh.output}} {{steps.refresh.data.link}}"},
            ],
        },
    },
]


def get_template(key: str) -> dict[str, Any] | None:
    return next((t for t in TEMPLATES if t["key"] == key), None)


# Names, descriptions and step names in the interface languages (the assistant's instructions stay in English; it
# answers in each person's language).
LOCALIZED: dict[str, dict[str, dict[str, Any]]] = {
    "ar": {
        "month_end_pack": {"name": "حزمة نهاية الشهر", "category": "الإقفال", "description": "في اليوم الثاني من كل شهر: إعداد مصنف الفعلي مقابل الموازنة للشهر الماضي، ومراجعته من المراجع، ثم طلب موافقة المراقب المالي، ثم إبلاغ الفريق بمكانه.", "steps": ["إعداد الحزمة", "موافقة المراقب المالي", "إبلاغ الفريق"]},
        "weekly_ar": {"name": "متابعة الذمم المدينة أسبوعياً", "category": "الذمم المدينة", "description": "كل يوم إثنين: عرض فواتير العملاء المتأخرة حسب العمر، وصياغة رسائل تذكير مهذبة لأكبرها (مسودات فقط، لا يُرسل شيء)، ونشر الملخص.", "steps": ["الفواتير المتأخرة", "هل هناك متأخرات؟", "نشر الملخص"]},
        "ap_due": {"name": "فواتير الموردين المستحقة هذا الأسبوع", "category": "الذمم الدائنة", "description": "كل يوم خميس: عرض فواتير الموردين المستحقة خلال الأيام السبعة القادمة مع النقد المطلوب، وطلب الموافقة على قائمة الدفعات.", "steps": ["الفواتير المستحقة", "الموافقة على قائمة الدفع", "تأكيد"]},
        "bank_statement_mail": {"name": "كشوف الحساب البنكية من البريد", "category": "النقد", "description": "عندما يرسل البنك كشف حساب بالبريد: حفظ المرفق في المستندات وتلخيص الرصيد الافتتاحي والختامي والبنود غير المعتادة.", "steps": ["قراءة الكشف", "مشاركة"]},
        "erp_webhook": {"name": "حدث من نظام آخر", "category": "التكاملات", "description": "يرسل نظام آخر (ERP أو مزوّد مدفوعات أو نموذج) حدثاً؛ فيفحصه المساعد ويطلب الموافقة عندما يحتاج الأمر إلى قرار بشري.", "steps": ["فحص الحدث", "هل يحتاج إلى قرار؟", "القرار"]},
        "refresh_file": {"name": "تحديث مصنف كل صباح", "category": "الملفات", "description": "إعادة تشغيل استعلامات مصنف مرتبط بالأدلة كل يوم عمل وإبلاغك عندما يجهز الإصدار الجديد. اختر المصنف قبل التفعيل.", "steps": ["تحديث المصنف", "إبلاغي"]},
    },
    "es": {
        "month_end_pack": {"name": "Paquete de cierre mensual", "category": "Cierre", "description": "El día 2 de cada mes: crea el libro de real frente a presupuesto del mes anterior, lo revisa el revisor, pide la aprobación del controller y avisa al equipo de dónde está.", "steps": ["Crear el paquete", "Aprobación del controller", "Avisar al equipo"]},
        "weekly_ar": {"name": "Seguimiento semanal de cobros", "category": "Cobros", "description": "Cada lunes: lista las facturas de clientes vencidas por antigüedad, redacta recordatorios corteses para las mayores (solo borradores, no se envía nada) y publica el resumen.", "steps": ["Facturas vencidas", "¿Hay algo vencido?", "Publicar el resumen"]},
        "ap_due": {"name": "Facturas de proveedores de esta semana", "category": "Pagos", "description": "Cada jueves: lista las facturas de proveedores que vencen en los próximos siete días con la caja necesaria y pide aprobación de la remesa de pagos.", "steps": ["Facturas que vencen", "Aprobar la remesa", "Confirmar"]},
        "bank_statement_mail": {"name": "Extractos bancarios por correo", "category": "Tesorería", "description": "Cuando el banco envía un extracto por correo: guarda el adjunto en Documentos y resume saldo inicial, saldo final y partidas inusuales.", "steps": ["Leer el extracto", "Compartir"]},
        "erp_webhook": {"name": "Evento de otro sistema", "category": "Integraciones", "description": "Otro sistema (un ERP, una pasarela de pago, un formulario) envía un evento; el asistente lo revisa y pide aprobación cuando hace falta una persona.", "steps": ["Revisar el evento", "¿Requiere decisión?", "Decidir"]},
        "refresh_file": {"name": "Actualizar un libro cada mañana", "category": "Archivos", "description": "Vuelve a ejecutar las consultas de un libro con evidencias cada día laborable y te avisa cuando la nueva versión está lista. Elige el libro antes de activarlo.", "steps": ["Actualizar el libro", "Avisarme"]},
    },
    "fr": {
        "month_end_pack": {"name": "Dossier de clôture mensuelle", "category": "Clôture", "description": "Le 2 de chaque mois : construit le classeur réel vs budget du mois précédent, le fait contrôler par le réviseur, demande l’approbation du contrôleur, puis indique à l’équipe où il se trouve.", "steps": ["Construire le dossier", "Approbation du contrôleur", "Prévenir l’équipe"]},
        "weekly_ar": {"name": "Suivi hebdomadaire des créances", "category": "Créances", "description": "Chaque lundi : liste les factures clients échues par ancienneté, rédige des relances courtoises pour les plus importantes (brouillons uniquement, rien n’est envoyé) et publie la synthèse.", "steps": ["Factures échues", "Des retards ?", "Publier la synthèse"]},
        "ap_due": {"name": "Factures fournisseurs de la semaine", "category": "Dettes fournisseurs", "description": "Chaque jeudi : liste les factures fournisseurs à échéance dans les sept jours avec la trésorerie nécessaire, et demande l’approbation de la liste de paiements.", "steps": ["Factures à échéance", "Approuver la liste", "Confirmer"]},
        "bank_statement_mail": {"name": "Relevés bancaires par e-mail", "category": "Trésorerie", "description": "Quand la banque envoie un relevé par e-mail : enregistre la pièce jointe dans les documents et résume solde d’ouverture, solde de clôture et opérations inhabituelles.", "steps": ["Lire le relevé", "Partager"]},
        "erp_webhook": {"name": "Événement d’un autre système", "category": "Intégrations", "description": "Un autre système (ERP, prestataire de paiement, formulaire) envoie un événement ; l’assistant le vérifie et demande une approbation quand une personne doit décider.", "steps": ["Vérifier l’événement", "Décision nécessaire ?", "Décider"]},
        "refresh_file": {"name": "Actualiser un classeur chaque matin", "category": "Fichiers", "description": "Relance chaque jour ouvré les requêtes d’un classeur justifié et vous prévient quand la nouvelle version est prête. Choisissez le classeur avant de l’activer.", "steps": ["Actualiser le classeur", "Me prévenir"]},
    },
}


def localized(lang: str | None) -> list[dict[str, Any]]:
    """The catalogue with names, descriptions and step names in ``lang`` (English when unknown)."""
    import copy

    table = LOCALIZED.get(lang or "", {})
    out = []
    for t in TEMPLATES:
        t = copy.deepcopy(t)
        loc = table.get(t["key"])
        if loc:
            t["name"], t["description"], t["category"] = loc["name"], loc["description"], loc["category"]
            for step, name in zip(t["definition"]["steps"], loc["steps"], strict=False):
                step["name"] = name
        out.append(t)
    return out
