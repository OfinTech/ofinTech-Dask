# Charts of accounts

**Send the chart of accounts your country's accountants actually use, and what each account is for.**

A ledger arrives with a class column in whatever words the customer's system writes — *Debtors*, *Creditors*,
*Sales*, *Gastos*, *ايرادات* — and account names in whatever language the company keeps its books in. Two steps
turn that into statements and ratios: the class word becomes one of the eleven classes the product knows, and
the class together with the name suggests a **role** (`cash`, `receivables`, `inventory`, `fixed_assets`,
`payables`, `short_term_debt`, `long_term_debt`, `equity`, `revenue`, `cogs`, `payroll`, `opex`,
`depreciation`, `interest`, `tax` and a few more). A person confirms or changes every suggestion — nothing here
is decided by a model — but a bad suggestion on a 400-account chart is an afternoon of clicking.

Both steps are word lists, and a word list is only as good as the charts it has been shown. That is this folder. The
other half is next door: [`fixtures/`](../README.md) keeps the *shape* of a country's bank statements, the same
way this keeps the shape of its chart.

## What a fixture is

```
fixtures/charts-of-accounts/<country>/<slug>/
    accounts.csv      Code, Account, Account class, Expected role
    expected.json     where the chart comes from, and any gaps it exposes
```

`accounts.csv` — twenty rows is plenty, as long as they cover the corners: a bank account, receivables and
payables in your country's words, stock, a fixed asset and its accumulated depreciation, a tax liability, a
loan that runs beyond a year, equity, revenue, cost of sales, payroll, depreciation, interest and tax.

```csv
Code,Account,Account class,Expected role
1200,Bank current account,Bank,cash
1100,Trade debtors,Debtors,receivables
2300,Bank loan (over one year),Liabilities,long_term_debt
```

**Account class** is the word *your* system writes, not the product's word. **Expected role** is what the
account is for, judged by you as the accountant — not what the product currently says.

## Gaps are the point, not a failure

When the product gets it wrong, that is the valuable fixture. Record it in `expected.json` instead of changing
the expectation:

```json
{
  "about": "One or two lines on what is distinctive about this chart.",
  "country": "gb",
  "source": "Invented, in the shape a UK trading company's ledger export has.",
  "invented": true,
  "gaps": {
    "classes": [{ "written": "Débiteurs", "expected": "Receivable", "why": "…" }],
    "roles":   [{ "code": "2300", "expected": "long_term_debt", "got": "short_term_debt", "why": "…" }]
  }
}
```

A recorded gap is **asserted to still reproduce**. When somebody extends the word list, the build fails and
says to delete the gap — so the list cannot quietly rot into a list of things that were fixed years ago. A gap
also needs a `why` worth reading: a gap without a reason is a hidden failing test, which is worse than a
failing test.

The two fixtures here already carry one each: a UK loan written *(over one year)* is suggested as **short-term**
debt, because the long-term words are «long-term», «long term» and «non-current»; and an account class written
in Arabic is not recognised at all, because the class words are English only while the role words beside them
are in four languages.

## Check it before you send it

```bash
python fixtures/charts-of-accounts/validate.py                                      # all of them
python fixtures/charts-of-accounts/validate.py fixtures/charts-of-accounts/fr/sme   # just yours
```

It prints, for every account, the class and role the product gives it, and marks every disagreement with your
`Expected role` — so you can see what to record as a gap. Then
`engine/tests/test_chart_corpus.py` holds the whole corpus on every build.

Invented data only: no real company's chart with its real account numbers if those are not public, and never a
customer or supplier name.

---

# مخططات الحسابات

**أرسل مخطط الحسابات الذي يستخدمه محاسبو بلدك فعلاً، وما الغرض من كل حساب.**

الدفتر يأتي وفيه عمود تصنيف بالكلمات التي يكتبها نظام العميل — *Debtors* و*Sales* و*ايرادات* — وأسماء حسابات
باللغة التي تُمسك بها الدفاتر. وخطوتان تحوّلان ذلك إلى قوائم ونسب: الكلمة تصبح واحداً من أحد عشر تصنيفاً يعرفها
المنتج، ثم يستنتج التصنيف مع الاسم **الدور** (نقد، ذمم مدينة، مخزون، أصول ثابتة، ذمم دائنة، دين قصير أو طويل
الأجل، حقوق ملكية، إيراد، تكلفة بيع، رواتب، مصاريف، إهلاك، فوائد، ضريبة). والشخص يؤكّد كل اقتراح أو يغيّره —
ولا شيء هنا يقرّره نموذج — لكن اقتراحاً خاطئاً في مخطط من ٤٠٠ حساب يعني ظهيرة كاملة من النقر.

والخطوتان قائمتا كلمات، وقائمة الكلمات لا تساوي أكثر من المخططات التي رأتها. وهذا هو عمل هذا المجلد.

**الشكل:** مجلد `fixtures/charts-of-accounts/<رمز البلد>/<الوصف>/` فيه `accounts.csv` بأعمدة: الرمز، اسم
الحساب، التصنيف **بكلمات نظامك أنت**، والدور المتوقّع **بحكمك أنت كمحاسب** لا بما يقوله المنتج اليوم. عشرون
صفاً تكفي إن غطّت الزوايا: حساب بنكي، وذمم مدينة ودائنة بكلمات بلدك، ومخزون، وأصل ثابت ومجمع إهلاكه، وضريبة
مستحقة، وقرض يمتدّ أكثر من سنة، وحقوق ملكية، وإيراد، وتكلفة بيع، ورواتب، وإهلاك، وفوائد، وضريبة دخل.

**والخلل هو المقصود لا الفشل:** إن أخطأ المنتج، سجّل ذلك في `expected.json` تحت `gaps` **ولا تغيّر التوقّع**.
وكل خلل مسجَّل **يُفترض أنه ما زال يحدث**: فإن أصلحه أحد سقط البناء وقال «احذفه من القائمة» — حتى لا تتحوّل
القائمة بصمت إلى أشياء أُصلحت منذ سنين. ويحتاج كل خلل سبباً يُقرأ، لأن خللاً بلا سبب اختبارٌ فاشل مخفيّ، وهذا
أسوأ من اختبار فاشل ظاهر.

والـ fixture الموجودان يحملان خللاً لكلٍّ منهما: قرض بريطاني مكتوب *(over one year)* يُقترح ديناً **قصير**
الأجل، لأن كلمات «طويل الأجل» المعروفة هي long-term وnon-current وما يقابلها؛ وتصنيف حساب مكتوب بالعربية لا
يُعرَف إطلاقاً، لأن كلمات التصنيف إنجليزية فقط بينما كلمات الأدوار بجانبها بأربع لغات.

**تحقّق قبل الإرسال:** `python fixtures/charts-of-accounts/validate.py` — يطبع لكل حساب التصنيف والدور كما
يعطيهما المنتج، ويؤشّر كل خلاف مع توقّعك، فترى ما يجب تسجيله خللاً. وبيانات مخترعة فقط: لا مخطط شركة حقيقية
بأرقام حساباتها إن لم تكن منشورة، ولا اسم عميل أو مورّد أبداً.
