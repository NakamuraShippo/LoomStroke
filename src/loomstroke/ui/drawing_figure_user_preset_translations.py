"""Labels for saved whole-body figure poses."""

# ruff: noqa: E501, RUF001, SIM905 - multilingual translation data

SOURCES = (
    "Saved poses|Select saved pose|Save current pose|Apply saved pose|Delete saved pose|Could not load pose presets|Pose presets|Save pose preset|Pose preset name|Could not save pose preset|Could not apply pose preset|Could not delete pose preset|Delete selected pose preset?|Choose a different name for this model.|Enter a name between 1 and 80 characters."
).split("|")
_ROWS = {
    "en": "|".join(SOURCES),
    "ja": "保存したポーズ|保存したポーズを選択|現在のポーズを保存|保存したポーズを適用|保存したポーズを削除|ポーズプリセットを読み込めませんでした|ポーズプリセット|ポーズプリセットを保存|ポーズプリセット名|ポーズプリセットを保存できませんでした|ポーズプリセットを適用できませんでした|ポーズプリセットを削除できませんでした|選択したポーズプリセットを削除しますか？|このモデルでは別の名前を指定してください。|名前は1文字以上80文字以内で入力してください。",
    "zh_CN": "已保存的姿势|选择已保存姿势|保存当前姿势|应用已保存姿势|删除已保存姿势|无法加载姿势预设|姿势预设|保存姿势预设|姿势预设名称|无法保存姿势预设|无法应用姿势预设|无法删除姿势预设|删除所选姿势预设？|请为此模型使用其他名称。|请输入1到80个字符的名称。",
    "zh_Hant": "已儲存的姿勢|選擇已儲存姿勢|儲存目前姿勢|套用已儲存姿勢|刪除已儲存姿勢|無法載入姿勢預設|姿勢預設|儲存姿勢預設|姿勢預設名稱|無法儲存姿勢預設|無法套用姿勢預設|無法刪除姿勢預設|刪除所選姿勢預設？|請為此模型使用其他名稱。|請輸入1到80個字元的名稱。",
    "ko": "저장된 포즈|저장된 포즈 선택|현재 포즈 저장|저장된 포즈 적용|저장된 포즈 삭제|포즈 프리셋을 불러올 수 없습니다|포즈 프리셋|포즈 프리셋 저장|포즈 프리셋 이름|포즈 프리셋을 저장할 수 없습니다|포즈 프리셋을 적용할 수 없습니다|포즈 프리셋을 삭제할 수 없습니다|선택한 포즈 프리셋을 삭제할까요?|이 모델에 다른 이름을 사용하세요.|이름은 1자에서 80자 사이로 입력하세요.",
    "es": "Poses guardadas|Elegir pose guardada|Guardar pose actual|Aplicar pose guardada|Eliminar pose guardada|No se pudieron cargar las poses guardadas|Poses guardadas|Guardar pose|Nombre de la pose|No se pudo guardar la pose|No se pudo aplicar la pose|No se pudo eliminar la pose|¿Eliminar la pose seleccionada?|Elige otro nombre para este modelo.|Escribe un nombre de entre 1 y 80 caracteres.",
    "ar": "وضعيات محفوظة|اختيار وضعية محفوظة|حفظ الوضعية الحالية|تطبيق الوضعية المحفوظة|حذف الوضعية المحفوظة|تعذر تحميل الوضعيات المحفوظة|إعدادات الوضعيات|حفظ إعداد وضعية|اسم إعداد الوضعية|تعذر حفظ إعداد الوضعية|تعذر تطبيق إعداد الوضعية|تعذر حذف إعداد الوضعية|هل تريد حذف إعداد الوضعية المحدد؟|اختر اسما آخر لهذا النموذج.|أدخل اسما يتراوح طوله بين 1 و80 حرفا.",
    "hi": "सहेजी गई मुद्राएँ|सहेजी मुद्रा चुनें|वर्तमान मुद्रा सहेजें|सहेजी मुद्रा लागू करें|सहेजी मुद्रा हटाएँ|मुद्रा प्रीसेट लोड नहीं हो सके|मुद्रा प्रीसेट|मुद्रा प्रीसेट सहेजें|मुद्रा प्रीसेट का नाम|मुद्रा प्रीसेट सहेजा नहीं जा सका|मुद्रा प्रीसेट लागू नहीं किया जा सका|मुद्रा प्रीसेट हटाया नहीं जा सका|चयनित मुद्रा प्रीसेट हटाएँ?|इस मॉडल के लिए कोई दूसरा नाम चुनें।|1 से 80 अक्षरों का नाम दर्ज करें।",
    "pt_BR": "Poses salvas|Escolher pose salva|Salvar pose atual|Aplicar pose salva|Excluir pose salva|Não foi possível carregar as poses salvas|Poses salvas|Salvar pose|Nome da pose|Não foi possível salvar a pose|Não foi possível aplicar a pose|Não foi possível excluir a pose|Excluir a pose selecionada?|Escolha outro nome para este modelo.|Digite um nome entre 1 e 80 caracteres.",
    "bn": "সংরক্ষিত ভঙ্গি|সংরক্ষিত ভঙ্গি বেছে নিন|বর্তমান ভঙ্গি সংরক্ষণ|সংরক্ষিত ভঙ্গি প্রয়োগ|সংরক্ষিত ভঙ্গি মুছুন|ভঙ্গির প্রিসেট লোড করা যায়নি|ভঙ্গির প্রিসেট|ভঙ্গির প্রিসেট সংরক্ষণ|ভঙ্গির প্রিসেটের নাম|ভঙ্গির প্রিসেট সংরক্ষণ করা যায়নি|ভঙ্গির প্রিসেট প্রয়োগ করা যায়নি|ভঙ্গির প্রিসেট মোছা যায়নি|নির্বাচিত ভঙ্গির প্রিসেট মুছবেন?|এই মডেলের জন্য অন্য নাম বেছে নিন।|1 থেকে 80 অক্ষরের নাম লিখুন।",
    "ru": "Сохранённые позы|Выбрать сохранённую позу|Сохранить текущую позу|Применить сохранённую позу|Удалить сохранённую позу|Не удалось загрузить сохранённые позы|Пресеты поз|Сохранить пресет позы|Название пресета позы|Не удалось сохранить пресет позы|Не удалось применить пресет позы|Не удалось удалить пресет позы|Удалить выбранный пресет позы?|Выберите другое имя для этой модели.|Введите имя длиной от 1 до 80 символов.",
}
_ROWS["zh_TW"] = _ROWS["zh_Hant"]
CATALOGS = {
    locale: dict(zip(SOURCES, row.split("|"), strict=True)) for locale, row in _ROWS.items()
}
