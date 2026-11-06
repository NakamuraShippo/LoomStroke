"""Drawing figure strings, shared by runtime fallback and compiled catalogs."""

# ruff: noqa: E501, RUF001, SIM905 - multilingual translation rows

from .drawing_figure_hand_translations import HAND_CATALOGS
from .drawing_figure_user_preset_translations import CATALOGS as USER_PRESET_CATALOGS

SOURCES = (
    "Add drawing figure|Drawing figure|Could not add drawing figure|Edit pose|Apply pose|Pose assistance|Pin / unpin joint|OpenPose layer|Drag joints; Alt-drag changes depth. Pink joints are pinned.|Export OpenPose|Could not export OpenPose|Standing|Arms out|Sitting|Walking|Crouching|Reaching"
).split("|")
TORSO = "Pelvis|Waist|Chest|Neck|Head".split("|")
LIMBS = "Collar|Shoulder|Elbow|Wrist|Hip|Knee|Ankle".split("|")
_ROWS = {
    "ja": (
        "デッサン人形を追加|デッサン人形|デッサン人形を追加できませんでした|ポーズ編集|ポーズを適用|ポーズ補正|関節を固定・解除|OpenPoseレイヤー|関節をドラッグして操作、Alt＋ドラッグで奥行きを変更。ピンクは固定した関節です。|OpenPoseを書き出す|OpenPoseを書き出せませんでした|立ち姿|腕を広げる|座り姿|歩き姿|しゃがみ姿|手を伸ばす",
        "骨盤|腰|胸|首|頭",
        "鎖骨|肩|肘|手首|股関節|膝|足首",
        "左{}",
        "右{}",
    ),
    "zh_CN": (
        "添加绘画人偶|绘画人偶|无法添加绘画人偶|编辑姿势|应用姿势|姿势辅助|固定／解除固定关节|OpenPose图层|拖动关节；按住Alt拖动可调整深度。粉色关节已固定。|导出OpenPose|无法导出OpenPose|站立|展开双臂|坐姿|行走|蹲姿|伸手",
        "骨盆|腰部|胸部|颈部|头部",
        "锁骨|肩部|肘部|手腕|髋部|膝盖|脚踝",
        "左{}",
        "右{}",
    ),
    "zh_Hant": (
        "新增繪畫人偶|繪畫人偶|無法新增繪畫人偶|編輯姿勢|套用姿勢|姿勢輔助|固定／解除固定關節|OpenPose圖層|拖曳關節；按住Alt拖曳可調整深度。粉紅色關節已固定。|匯出OpenPose|無法匯出OpenPose|站立|展開雙臂|坐姿|行走|蹲姿|伸手",
        "骨盆|腰部|胸部|頸部|頭部",
        "鎖骨|肩部|肘部|手腕|髖部|膝蓋|腳踝",
        "左{}",
        "右{}",
    ),
    "ko": (
        "데생 인형 추가|데생 인형|데생 인형을 추가하지 못했습니다|포즈 편집|포즈 적용|포즈 보정|관절 고정 / 해제|OpenPose 레이어|관절을 드래그하세요. Alt를 누른 채 드래그하면 깊이가 변경됩니다. 분홍색 관절은 고정되어 있습니다.|OpenPose 내보내기|OpenPose를 내보내지 못했습니다|서기|팔 벌리기|앉기|걷기|쪼그려 앉기|손 뻗기",
        "골반|허리|가슴|목|머리",
        "쇄골|어깨|팔꿈치|손목|고관절|무릎|발목",
        "왼쪽 {}",
        "오른쪽 {}",
    ),
    "es": (
        "Añadir figura de dibujo|Figura de dibujo|No se pudo añadir la figura de dibujo|Editar pose|Aplicar pose|Asistencia de pose|Fijar / liberar articulación|Capa OpenPose|Arrastra las articulaciones; Alt y arrastrar cambia la profundidad. Las articulaciones rosas están fijadas.|Exportar OpenPose|No se pudo exportar OpenPose|De pie|Brazos abiertos|Sentado|Caminando|En cuclillas|Extendiendo el brazo",
        "Pelvis|Cintura|Pecho|Cuello|Cabeza",
        "Clavícula|Hombro|Codo|Muñeca|Cadera|Rodilla|Tobillo",
        "Izquierda: {}",
        "Derecha: {}",
    ),
    "ar": (
        "إضافة مجسم للرسم|مجسم الرسم|تعذرت إضافة مجسم الرسم|تحرير الوضعية|تطبيق الوضعية|مساعدة الوضعية|تثبيت المفصل / تحريره|طبقة OpenPose|اسحب المفاصل؛ السحب مع Alt يغيّر العمق. المفاصل الوردية مثبتة.|تصدير OpenPose|تعذر تصدير OpenPose|وقوف|ذراعان ممدودتان|جلوس|مشي|قرفصاء|مد الذراع",
        "الحوض|الخصر|الصدر|الرقبة|الرأس",
        "الترقوة|الكتف|المرفق|المعصم|الورك|الركبة|الكاحل",
        "يسار: {}",
        "يمين: {}",
    ),
    "hi": (
        "ड्रॉइंग पुतला जोड़ें|ड्रॉइंग पुतला|ड्रॉइंग पुतला नहीं जोड़ा जा सका|मुद्रा संपादित करें|मुद्रा लागू करें|मुद्रा सहायता|जोड़ स्थिर करें / छोड़ें|OpenPose परत|जोड़ों को खींचें; Alt के साथ खींचने से गहराई बदलती है। गुलाबी जोड़ स्थिर हैं।|OpenPose निर्यात करें|OpenPose निर्यात नहीं हो सका|खड़े होना|बाहें फैलाना|बैठना|चलना|उकड़ूँ बैठना|हाथ बढ़ाना",
        "श्रोणि|कमर|छाती|गर्दन|सिर",
        "हंसली|कंधा|कोहनी|कलाई|कूल्हा|घुटना|टखना",
        "बायाँ: {}",
        "दायाँ: {}",
    ),
    "pt_BR": (
        "Adicionar manequim de desenho|Manequim de desenho|Não foi possível adicionar o manequim|Editar pose|Aplicar pose|Assistência de pose|Fixar / liberar articulação|Camada OpenPose|Arraste as articulações; Alt e arrastar altera a profundidade. As articulações rosas estão fixadas.|Exportar OpenPose|Não foi possível exportar OpenPose|Em pé|Braços abertos|Sentado|Caminhando|Agachado|Estendendo o braço",
        "Pelve|Cintura|Peito|Pescoço|Cabeça",
        "Clavícula|Ombro|Cotovelo|Pulso|Quadril|Joelho|Tornozelo",
        "Esquerda: {}",
        "Direita: {}",
    ),
    "bn": (
        "আঁকার পুতুল যোগ করুন|আঁকার পুতুল|আঁকার পুতুল যোগ করা যায়নি|ভঙ্গি সম্পাদনা করুন|ভঙ্গি প্রয়োগ করুন|ভঙ্গি সহায়তা|জয়েন্ট স্থির করুন / ছাড়ুন|OpenPose স্তর|জয়েন্ট টানুন; Alt ধরে টানলে গভীরতা বদলায়। গোলাপি জয়েন্ট স্থির করা আছে।|OpenPose রপ্তানি করুন|OpenPose রপ্তানি করা যায়নি|দাঁড়ানো|হাত ছড়ানো|বসা|হাঁটা|উবু হয়ে বসা|হাত বাড়ানো",
        "শ্রোণি|কোমর|বুক|ঘাড়|মাথা",
        "কলারবোন|কাঁধ|কনুই|কবজি|নিতম্ব|হাঁটু|গোড়ালি",
        "বাম: {}",
        "ডান: {}",
    ),
    "ru": (
        "Добавить манекен для рисования|Манекен для рисования|Не удалось добавить манекен|Изменить позу|Применить позу|Помощь при позировании|Закрепить / освободить сустав|Слой OpenPose|Перетаскивайте суставы; Alt с перетаскиванием меняет глубину. Розовые суставы закреплены.|Экспорт OpenPose|Не удалось экспортировать OpenPose|Стоя|Руки в стороны|Сидя|Ходьба|На корточках|Вытянутая рука",
        "Таз|Талия|Грудь|Шея|Голова",
        "Ключица|Плечо|Локоть|Запястье|Бедро|Колено|Лодыжка",
        "Слева: {}",
        "Справа: {}",
    ),
}
_ROWS["zh_TW"] = _ROWS["zh_Hant"]


def _catalogs() -> dict[str, dict[str, str]]:
    catalogs = {"en": dict.fromkeys(SOURCES + TORSO, "")}
    catalogs["en"] = {key: key for key in SOURCES + TORSO}
    for side in ("Left", "Right"):
        catalogs["en"].update({f"{side} {limb}": f"{side} {limb}" for limb in LIMBS})
    for locale, (actions, torso, limbs, left, right) in _ROWS.items():
        catalog = dict(zip(SOURCES, actions.split("|"), strict=True))
        catalog.update(zip(TORSO, torso.split("|"), strict=True))
        for side, template in (("Left", left), ("Right", right)):
            catalog.update(
                {
                    f"{side} {key}": template.format(value)
                    for key, value in zip(LIMBS, limbs.split("|"), strict=True)
                }
            )
        catalogs[locale] = catalog
    return catalogs


CATALOGS = _catalogs()

ROTATION_HINT = (
    "Drag joints to move; drag XYZ rings to rotate. Alt-drag changes depth. Pink joints are pinned."
)
_ROTATION_HINTS = {
    "en": ROTATION_HINT,
    "ja": "関節をドラッグして移動、XYZリングをドラッグして回転。Alt＋ドラッグで奥行きを変更。ピンクは固定した関節です。",
    "zh_CN": "拖动关节移动，拖动XYZ圆环旋转。按住Alt拖动可调整深度。粉色关节已固定。",
    "zh_Hant": "拖曳關節以移動，拖曳XYZ圓環以旋轉。按住Alt拖曳可調整深度。粉紅色關節已固定。",
    "ko": "관절을 드래그하여 이동하고 XYZ 링을 드래그하여 회전합니다. Alt를 누른 채 드래그하면 깊이가 변경됩니다. 분홍색 관절은 고정되어 있습니다.",
    "es": "Arrastra las articulaciones para moverlas y los anillos XYZ para girarlas. Alt al arrastrar cambia la profundidad. Las articulaciones rosas están fijadas.",
    "ar": "اسحب المفاصل لتحريكها وحلقات XYZ لتدويرها. السحب مع Alt يغير العمق. المفاصل الوردية مثبتة.",
    "hi": "जोड़ों को खींचकर हिलाएँ और XYZ रिंग खींचकर घुमाएँ। Alt के साथ खींचने से गहराई बदलती है। गुलाबी जोड़ स्थिर हैं।",
    "pt_BR": "Arraste as articulações para mover e os anéis XYZ para girar. Alt ao arrastar altera a profundidade. Articulações rosas estão fixadas.",
    "bn": "জয়েন্ট টেনে সরান এবং XYZ রিং টেনে ঘোরান। Alt চেপে টানলে গভীরতা বদলায়। গোলাপি জয়েন্ট স্থির করা আছে।",
    "ru": "Перетаскивайте суставы для перемещения, а кольца XYZ — для вращения. Alt с перетаскиванием меняет глубину. Розовые суставы закреплены.",
}
for _locale, _hint in _ROTATION_HINTS.items():
    CATALOGS[_locale][ROTATION_HINT] = _hint

_RESET_PART_TEXTS = {
    "en": "Reset selected part",
    "ja": "選択部位を初期姿勢に戻す",
    "zh_CN": "重置所选部位",
    "zh_Hant": "重設所選部位",
    "zh_TW": "重設所選部位",
    "ko": "선택한 부위 초기화",
    "es": "Restablecer parte seleccionada",
    "ar": "إعادة ضبط الجزء المحدد",
    "hi": "चयनित अंग रीसेट करें",
    "pt_BR": "Redefinir parte selecionada",
    "bn": "নির্বাচিত অংশ রিসেট করুন",
    "ru": "Сбросить выбранную часть",
}
for _locale, _text in _RESET_PART_TEXTS.items():
    CATALOGS[_locale]["Reset selected part"] = _text

for _locale, _hand_catalog in HAND_CATALOGS.items():
    CATALOGS[_locale].update(_hand_catalog)

for _locale, _pose_catalog in USER_PRESET_CATALOGS.items():
    CATALOGS[_locale].update(_pose_catalog)
