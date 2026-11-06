"""Hand editor strings for runtime fallback and compiled catalogs."""

# ruff: noqa: E501, RUF001, SIM905 - multilingual translation data

from .drawing_figure_preset_translations import PRESET_CATALOGS

HAND_SOURCES = "Edit selected hand|Hand pose|Finger|Finger curl|Finger spread|Cup palm|Joint|Reset hand|Thumb|Index finger|Middle finger|Ring finger|Little finger|Palm|Wrist|Open hand|Relaxed hand|Fist|Pointing hand|Peace sign".split(
    "|"
)
_ROWS = {
    "en": "|".join(HAND_SOURCES),
    "ja": "選択中の手を編集|手のポーズ|指|曲げ|開き|手のひらを丸める|関節|手を初期姿勢に戻す|親指|人差し指|中指|薬指|小指|手のひら|手首|開いた手|力を抜いた手|握りこぶし|指差し|ピース",
    "zh_CN": "编辑所选手部|手部姿势|手指|弯曲|张开|弯曲手掌|关节|重置手部姿势|拇指|食指|中指|无名指|小指|手掌|手腕|张开的手|放松的手|握拳|指向|胜利手势",
    "zh_Hant": "編輯所選手部|手部姿勢|手指|彎曲|張開|彎曲手掌|關節|重設手部姿勢|拇指|食指|中指|無名指|小指|手掌|手腕|張開的手|放鬆的手|握拳|指向|勝利手勢",
    "ko": "선택한 손 편집|손 포즈|손가락|굽힘|벌림|손바닥 오므리기|관절|손 포즈 초기화|엄지|검지|중지|약지|새끼손가락|손바닥|손목|편 손|힘을 뺀 손|주먹|가리키는 손|브이 사인",
    "es": "Editar mano seleccionada|Pose de la mano|Dedo|Flexión|Separación|Ahuecar la palma|Articulación|Restablecer mano|Pulgar|Índice|Dedo medio|Anular|Meñique|Palma|Muñeca|Mano abierta|Mano relajada|Puño|Mano señalando|Señal de paz",
    "ar": "تحرير اليد المحددة|وضعية اليد|الإصبع|الثني|التباعد|تقويس راحة اليد|المفصل|إعادة ضبط اليد|الإبهام|السبابة|الوسطى|البنصر|الخنصر|راحة اليد|المعصم|يد مفتوحة|يد مسترخية|قبضة|يد تشير|إشارة السلام",
    "hi": "चयनित हाथ संपादित करें|हाथ की मुद्रा|उंगली|मोड़|फैलाव|हथेली मोड़ें|जोड़|हाथ रीसेट करें|अंगूठा|तर्जनी|मध्यमा|अनामिका|कनिष्ठा|हथेली|कलाई|खुला हाथ|ढीला हाथ|मुट्ठी|इशारा करता हाथ|शांति चिह्न",
    "pt_BR": "Editar mão selecionada|Pose da mão|Dedo|Flexão|Abertura|Encurvar a palma|Articulação|Redefinir mão|Polegar|Indicador|Dedo médio|Anelar|Dedo mínimo|Palma|Pulso|Mão aberta|Mão relaxada|Punho fechado|Mão apontando|Sinal de paz",
    "bn": "নির্বাচিত হাত সম্পাদনা করুন|হাতের ভঙ্গি|আঙুল|বাঁক|ফাঁক|তালু বাঁকান|জয়েন্ট|হাত রিসেট করুন|বুড়ো আঙুল|তর্জনী|মধ্যমা|অনামিকা|কনিষ্ঠা|তালু|কবজি|খোলা হাত|শিথিল হাত|মুষ্টি|ইশারা করা হাত|শান্তির চিহ্ন",
    "ru": "Редактировать выбранную кисть|Поза кисти|Палец|Сгибание|Разведение|Согнуть ладонь|Сустав|Сбросить позу кисти|Большой палец|Указательный палец|Средний палец|Безымянный палец|Мизинец|Ладонь|Запястье|Открытая кисть|Расслабленная кисть|Кулак|Указующий жест|Жест мира",
}
_ROWS["zh_TW"] = _ROWS["zh_Hant"]
HAND_CATALOGS = {
    locale: dict(zip(HAND_SOURCES, row.split("|"), strict=True)) for locale, row in _ROWS.items()
}
for _locale, _presets in PRESET_CATALOGS.items():
    HAND_CATALOGS[_locale].update(_presets)
