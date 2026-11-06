"""Localized names for the authored hand preset library."""

# ruff: noqa: E501, RUF001, SIM905 - multilingual data

from loomstroke.core.drawing_figures.hand_presets import PRESET_CATEGORIES, PRESET_DEFINITIONS

PRESET_SOURCES = (
    [
        value.label
        for key, value in PRESET_DEFINITIONS.items()
        if key not in {"open", "relaxed", "fist", "point", "peace"}
    ]
    + list(PRESET_CATEGORIES.values())
    + ["All hand poses"]
)
_ROWS = {
    "en": "|".join(PRESET_SOURCES),
    "ja": "指をそろえる|指を広げる|平らな手|軽く握る|強く握る|半開きの手|すくう手|深くすくう手|サムズアップ|OKサイン|角のサイン|シャカサイン|三本の指を伸ばす|四本の指を伸ばす|親指と人差し指|親指・人差し指・中指|指を交差|指ハート|指で手招き|制止の手|数える・一|数える・二|数える・三|数える・四|数える・五|小さくつまむ|大きくつまむ|三点つまみ|細かい物をつまむ|ペンを持つ|筆を持つ|筒を握る|細い柄を握る|ボールを持つ|大きなボールを持つ|ひっかけて持つ|鍵をつまむ|かぎ爪の手|そっと触れる|指先で触れる|布をつまむ|しなやかな手|力を入れた手|基本の手|ジェスチャー|指で数える|握る・持つ|表現の手|すべての手のポーズ",
    "zh_CN": "并拢手指|张开手指|平放的手|轻握拳|紧握拳|半闭合的手|捧起的手|深拢的手|竖起拇指|OK手势|犄角手势|沙卡手势|伸出三指|伸出四指|拇指和食指|拇指、食指和中指|交叉手指|手指爱心|勾指招手|停止手势|数一|数二|数三|数四|数五|小幅捏取|大幅捏取|三指捏取|精细抓握|握笔|握画笔|握圆柱|握细柄|握球|握大球|钩握|捏钥匙|爪形手|轻触|指尖触碰|捏布料|柔美的手|紧张的手|基本手势|表达手势|手指数数|抓握姿势|表现性手势|全部手部姿势",
    "zh_Hant": "併攏手指|張開手指|平放的手|輕握拳|緊握拳|半閉合的手|捧起的手|深攏的手|豎起拇指|OK手勢|犄角手勢|沙卡手勢|伸出三指|伸出四指|拇指和食指|拇指、食指和中指|交叉手指|手指愛心|勾指招手|停止手勢|數一|數二|數三|數四|數五|小幅捏取|大幅捏取|三指捏取|精細抓握|握筆|握畫筆|握圓柱|握細柄|握球|握大球|鉤握|捏鑰匙|爪形手|輕觸|指尖觸碰|捏布料|柔美的手|緊張的手|基本手勢|表達手勢|手指數數|抓握姿勢|表現性手勢|全部手部姿勢",
    "ko": "손가락 모으기|손가락 펼치기|평평한 손|가볍게 쥔 주먹|꽉 쥔 주먹|반쯤 쥔 손|오므린 손|깊게 오므린 손|엄지 올리기|OK 사인|뿔 모양 손|샤카 사인|세 손가락 펴기|네 손가락 펴기|엄지와 검지|엄지·검지·중지|손가락 교차|손가락 하트|손가락으로 부르기|정지 손짓|하나 세기|둘 세기|셋 세기|넷 세기|다섯 세기|작게 집기|넓게 집기|세 손가락 집기|정밀하게 잡기|펜 잡기|붓 잡기|원통 잡기|가는 손잡이 잡기|공 잡기|큰 공 잡기|갈고리 모양 잡기|열쇠 집기|발톱 모양 손|가볍게 닿기|손끝으로 닿기|천 집기|섬세한 손|힘을 준 손|기본 손 포즈|손짓|손가락으로 세기|잡는 포즈|표현적인 손|모든 손 포즈",
    "es": "Dedos juntos|Dedos separados|Mano plana|Puño suave|Puño apretado|Mano semicerrada|Mano ahuecada|Mano muy ahuecada|Pulgar arriba|Signo de OK|Signo de cuernos|Signo shaka|Tres dedos extendidos|Cuatro dedos extendidos|Pulgar e índice|Pulgar, índice y medio|Dedos cruzados|Corazón con los dedos|Dedo llamando|Gesto de alto|Contar uno|Contar dos|Contar tres|Contar cuatro|Contar cinco|Pinza pequeña|Pinza amplia|Pinza de tres dedos|Agarre de precisión|Agarrar un bolígrafo|Agarrar un pincel|Agarre cilíndrico|Agarrar un mango fino|Agarrar una pelota|Agarrar una pelota grande|Agarre de gancho|Pinza de llave|Mano en garra|Toque suave|Toque con la punta|Pinzar tela|Mano delicada|Mano tensa|Poses básicas de mano|Gestos de mano|Contar con los dedos|Poses de agarre|Manos expresivas|Todas las poses de mano",
    "ar": "أصابع مضمومة|أصابع مفرودة|يد مسطحة|قبضة خفيفة|قبضة محكمة|يد نصف مغلقة|يد مقعرة|يد مقعرة بعمق|إبهام مرفوع|إشارة OK|إشارة القرون|إشارة شاكا|ثلاثة أصابع ممدودة|أربعة أصابع ممدودة|الإبهام والسبابة|الإبهام والسبابة والوسطى|أصابع متقاطعة|قلب بالأصابع|إصبع يستدعي|إشارة توقف|عد واحد|عد اثنين|عد ثلاثة|عد أربعة|عد خمسة|قرصة صغيرة|قرصة واسعة|قرصة بثلاثة أصابع|قبضة دقيقة|إمساك قلم|إمساك فرشاة|قبضة أسطوانية|إمساك مقبض رفيع|إمساك كرة|إمساك كرة كبيرة|قبضة خطافية|قرصة مفتاح|يد كالمخلب|لمسة لطيفة|لمس بأطراف الأصابع|قرص القماش|يد رقيقة|يد متوترة|وضعيات يد أساسية|إيماءات اليد|العد بالأصابع|وضعيات الإمساك|أيد تعبيرية|كل وضعيات اليد",
    "hi": "उंगलियाँ साथ|उंगलियाँ फैली हुई|सपाट हाथ|हल्की मुट्ठी|कसी हुई मुट्ठी|आधा बंद हाथ|कटोरी जैसा हाथ|गहरा कटोरी जैसा हाथ|अंगूठा ऊपर|OK संकेत|सींग का संकेत|शाका संकेत|तीन उंगलियाँ सीधी|चार उंगलियाँ सीधी|अंगूठा और तर्जनी|अंगूठा, तर्जनी और मध्यमा|उंगलियाँ क्रॉस|उंगलियों का दिल|उंगली से बुलाना|रुकने का संकेत|एक गिनें|दो गिनें|तीन गिनें|चार गिनें|पाँच गिनें|छोटी चुटकी|चौड़ी चुटकी|तीन उंगलियों की चुटकी|बारीक पकड़|कलम पकड़ना|कूची पकड़ना|बेलनाकार पकड़|पतला हत्था पकड़ना|गेंद पकड़ना|बड़ी गेंद पकड़ना|हुक जैसी पकड़|चाबी की पकड़|पंजे जैसा हाथ|हल्का स्पर्श|उंगलियों के सिरों से स्पर्श|कपड़ा चुटकी में पकड़ना|कोमल हाथ|तनाव वाला हाथ|हाथ की मूल मुद्राएँ|हाथ के संकेत|उंगलियों से गिनना|पकड़ने की मुद्राएँ|अभिव्यक्तिपूर्ण हाथ|हाथ की सभी मुद्राएँ",
    "pt_BR": "Dedos juntos|Dedos abertos|Mão plana|Punho suave|Punho apertado|Mão semifechada|Mão em concha|Mão em concha profunda|Polegar para cima|Sinal de OK|Sinal de chifres|Sinal shaka|Três dedos estendidos|Quatro dedos estendidos|Polegar e indicador|Polegar, indicador e médio|Dedos cruzados|Coração com os dedos|Dedo chamando|Gesto de pare|Contar um|Contar dois|Contar três|Contar quatro|Contar cinco|Pinça pequena|Pinça ampla|Pinça de três dedos|Preensão de precisão|Segurar caneta|Segurar pincel|Preensão cilíndrica|Segurar cabo fino|Segurar bola|Segurar bola grande|Preensão em gancho|Pinça de chave|Mão em garra|Toque suave|Toque com a ponta|Pinçar tecido|Mão delicada|Mão tensa|Poses básicas de mão|Gestos de mão|Contagem com os dedos|Poses de preensão|Mãos expressivas|Todas as poses de mão",
    "bn": "আঙুল একসঙ্গে|আঙুল ছড়ানো|সমতল হাত|হালকা মুষ্টি|শক্ত মুষ্টি|আধা বন্ধ হাত|কাপের মতো হাত|গভীর কাপের মতো হাত|বুড়ো আঙুল ওপরে|OK চিহ্ন|শিংয়ের চিহ্ন|শাকা চিহ্ন|তিনটি আঙুল সোজা|চারটি আঙুল সোজা|বুড়ো আঙুল ও তর্জনী|বুড়ো আঙুল, তর্জনী ও মধ্যমা|আঙুল আড়াআড়ি|আঙুলের হৃদয়|আঙুল দিয়ে ডাকা|থামার ইশারা|এক গণনা|দুই গণনা|তিন গণনা|চার গণনা|পাঁচ গণনা|ছোট চিমটি|চওড়া চিমটি|তিন আঙুলের চিমটি|সূক্ষ্মভাবে ধরা|কলম ধরা|তুলি ধরা|বেলন ধরে রাখা|সরু হাতল ধরা|বল ধরা|বড় বল ধরা|হুকের মতো ধরা|চাবি ধরা|নখরের মতো হাত|কোমল স্পর্শ|আঙুলের ডগায় স্পর্শ|কাপড় চিমটি দিয়ে ধরা|কোমল হাত|টানটান হাত|হাতের মৌলিক ভঙ্গি|হাতের ইশারা|আঙুলে গণনা|ধরার ভঙ্গি|অভিব্যক্তিপূর্ণ হাত|হাতের সব ভঙ্গি",
    "ru": "Пальцы вместе|Пальцы врозь|Плоская ладонь|Мягкий кулак|Сжатый кулак|Полусжатая кисть|Ладонь лодочкой|Глубокая лодочка|Большой палец вверх|Жест OK|Жест рогов|Жест шака|Три вытянутых пальца|Четыре вытянутых пальца|Большой и указательный|Большой, указательный и средний|Скрещённые пальцы|Сердце пальцами|Подзывающий палец|Жест остановки|Счёт один|Счёт два|Счёт три|Счёт четыре|Счёт пять|Малый щипок|Широкий щипок|Трёхпальцевый щипок|Точный захват|Захват ручки|Захват кисти|Цилиндрический захват|Захват тонкой рукоятки|Захват мяча|Захват большого мяча|Крюковой захват|Захват ключа|Кисть когтем|Лёгкое касание|Касание кончиками пальцев|Щипок ткани|Изящная кисть|Напряжённая кисть|Основные позы кисти|Жесты кисти|Счёт на пальцах|Позы захвата|Выразительные кисти|Все позы кисти",
}
_ROWS["zh_TW"] = _ROWS["zh_Hant"]
PRESET_CATALOGS = {
    locale: dict(zip(PRESET_SOURCES, row.split("|"), strict=True)) for locale, row in _ROWS.items()
}
