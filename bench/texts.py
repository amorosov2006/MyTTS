"""Benchmark / listening-test texts (original prose written for this project).

PROSE_* segments are already "normalized" (numbers spelled out) and sized like
real pipeline segments (1-3 sentences, 120-350 chars).
TRAPS_* contain raw constructs the text normalizer will later have to handle;
they are rendered once so we can hear how the model copes without help.
"""

PROSE_RU = [
    "Поезд пришёл на станцию поздно вечером, когда над рекой уже поднимался туман. "
    "На платформе не было никого, кроме старого сторожа с фонарём.",
    "Анна долго стояла у окна и смотрела, как снег медленно засыпает двор. "
    "Ей казалось, что вместе со снегом в дом приходит тишина, которой она так ждала.",
    "— Вы опоздали, — сказал он негромко, не поднимая глаз от книги. — "
    "Впрочем, это уже не имеет никакого значения.",
    "В тысяча восемьсот девяносто первом году семья переехала в Москву, "
    "и отец впервые за много лет получил постоянное место в университете.",
    "Лес за деревней был густой и тёмный; даже в полдень под елями стоял прохладный зелёный полумрак, "
    "и пахло смолой, грибами и прошлогодней листвой.",
    "Он написал письмо, перечитал его дважды, а потом аккуратно сложил пополам и спрятал в ящик стола, "
    "так и не решившись отправить.",
    "Утром выяснилось, что дорогу размыло дождями, и ехать дальше можно было только верхом "
    "или пешком, через старый мост у мельницы.",
    "Она засмеялась, и в этом смехе было столько свободы и лёгкости, что все вокруг невольно улыбнулись.",
]

PROSE_EN = [
    "The train reached the station late in the evening, when mist was already rising over the river. "
    "There was no one on the platform except an old watchman with a lantern.",
    "Anna stood at the window for a long time, watching the snow slowly cover the yard. "
    "It seemed to her that the silence she had waited for was coming into the house with it.",
    "\"You are late,\" he said quietly, without raising his eyes from the book. "
    "\"Not that it matters anymore.\"",
    "In eighteen ninety-one the family moved to London, and for the first time in years "
    "her father held a permanent position at the university.",
    "The forest beyond the village was dense and dark; even at noon a cool green twilight lay beneath the firs, "
    "and the air smelled of resin, mushrooms and last year's leaves.",
    "He wrote the letter, read it through twice, then folded it neatly in half and hid it in the desk drawer, "
    "never quite daring to send it.",
    "In the morning it turned out that the rains had washed away the road, and the only way forward "
    "was on horseback or on foot, across the old bridge by the mill.",
    "She laughed, and there was so much freedom and lightness in that laugh that everyone around her smiled.",
]

TRAPS_RU = [
    "В 1891 г. он купил 3 дома на ул. Садовой, д. 15, за 2500 руб.",
    "Замок на двери старого замка давно заржавел. Все были в сборе, но всё было не так.",
    "Ее муж, Петр Семенович, еще спал.",  # ё written as е
    "Он открыл ноутбук, запустил Windows и прочитал письмо от Google.",
]

TRAPS_EN = [
    "On Jan. 5, 1891, Dr. Smith paid $2,500 for 3 houses on St. James St.",
    "I read the book yesterday; now I read the news. The wind wound around the tower.",
]

# Short reference transcripts spoken by the designed narrators (VoiceDesign -> Base clone).
REF_TEXT_RU = (
    "Здравствуйте. Меня зовут Алексей, и сегодня я прочитаю для вас эту книгу. "
    "Устраивайтесь поудобнее, мы начинаем."
)
REF_TEXT_EN = (
    "Hello. My name is Alex, and today I will be reading this book for you. "
    "Make yourself comfortable, and let us begin."
)
