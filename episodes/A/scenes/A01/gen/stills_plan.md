# A01 静止画の生成計画（承認待ち）

モデル: Nano Banana Pro（Higgsfield、2k、16:9）。1枚2クレジット。57枚で114クレジット。指示文の全文は stills_plan.yaml。

最初に居間の背景の基準（world:living-room-plate）を1枚作り、居間のカットはそれを参照画像にする。

| # | 対象 | 種別 | 参照画像 | 内容 |
| --- | --- | --- | --- | --- |
| 0 | world:living-room-plate | 背景 | witch-house-living_ingame_01、witch-house-living_ingame_02、witch-house-exterior_ingame_01 | 背景の基準（人物なし、丸窓を足す） |
| 1 | A01-001 | K | witch-house-exterior_ingame_01、swamp_ingame_02 | 朝靄。翠の水面に泡が浮かび、弾ける |
| 2 | A01-002 | M | living-room-plate、arlecchino_official_03、bisco_official_03、bisco_official_01、hundred-knight_official_02 | アルレッキーノが丸テーブルの皿の位置を指先で直す。奥のソファにビスコ、テーブルの右に百騎兵 |
| 3 | A01-003 | M | living-room-plate、bisco_official_03、bisco_official_01 | ビスコがバターを端から端まで均一に塗る |
| 4 | A01-004 | M | living-room-plate、hundred-knight_official_02 | 目玉焼きを見つめる。12.3「おい」で天井を見上げる |
| 5 | A01-005a | A | living-room-plate、metallica_official_02、metallica_official_01 | 階段の上に現れたメタリカが、招待状を掲げたまま3段飛ばしで駆け下りる。片手で帽子を押さえ、マントとスカートが遅れてはためく |
| 6 | A01-005b | A | living-room-plate、metallica_official_02、metallica_official_01 | 14.8 最後の段から床へ着地（膝が沈み、帽子が跳ねる）→止まらずにテーブルへ大股3歩 |
| 7 | A01-005c | A | living-room-plate、metallica_official_02、metallica_official_01、hundred-knight_official_02 | 16.2 テーブルに手を叩きつけ（皿と目玉焼きが跳ねる）、招待状を百騎兵の鼻先へ。17.2「だぞ？」で招待状の上から身を乗り出し、にやりと顔を見せる |
| 8 | A01-006 | S | living-room-plate、hundred-knight_official_02 | 首をかしげる |
| 9 | A01-007 | K | living-room-plate、metallica_official_02、metallica_official_01 | 「キヒヒ！」 |
| 10 | A01-007a | K | living-room-plate、metallica_official_02、metallica_official_01、hundred-knight_official_02 | 身をかがめて百騎兵と目の高さを合わせ、招待状をひらひら振る |
| 11 | A01-008 | M | living-room-plate、metallica_official_02、metallica_official_01 | 「よかろう！」のあとの0.6秒の間で、床に積んだ本の山に片足をかけて演説の姿勢 |
| 12 | A01-009 | M | living-room-plate、metallica_official_02、metallica_official_01 | 目を細め、招待状を胸に当てて悦に入る。「一……流」の途切れ（34.9）で目を開け、こちらを見据えてにやりとする |
| 13 | A01-010 | M | living-room-plate、metallica_official_02、metallica_official_01 | 招待状を丸窓の方へ大きく振り出し、「沼」（39.3）で胸を張る |
| 14 | A01-010a | M | living-room-plate、hundred-knight_official_02 | （声はオフ）フォークで黄身をつつき、二度目の「一流」（40.6）で割れて広がる |
| 15 | A01-010b | K | living-room-plate、metallica_official_02、metallica_official_01 | 「大魔女」（42.5）で招待状を天井へ掲げる |
| 16 | A01-011 | K | living-room-plate、bisco_official_03、bisco_official_01 | パンを噛みながら、ちらりと招待状を見上げる。耳が小さく動く |
| 17 | A01-012 | M | living-room-plate、metallica_official_02、metallica_official_01 | 「あん？」→直後の0.9秒の間で、目だけがビスコの方へ走る |
| 18 | A01-012a | M | living-room-plate、metallica_official_02、metallica_official_01、bisco_official_03、bisco_official_01 | テーブル越しに身を乗り出し、招待状をビスコの鼻先へ |
| 19 | A01-013 | M | living-room-plate、bisco_official_03、bisco_official_01 | 「呼ぶな！」（56.1）で招待状を手の甲で押しのける |
| 20 | A01-013a | K | living-room-plate、metallica_official_02、metallica_official_01 | （声はオフ）聞き流して、押しのけられた招待状の縁を指で直す |
| 21 | A01-014 | K | living-room-plate、bisco_official_03、bisco_official_01 | パンに目を戻しながら |
| 22 | A01-015 | K | living-room-plate、metallica_official_02、metallica_official_01 | （声はオフ）得意げな笑顔が固まっていき、「そればっかりだぞ」で顔が赤くなる |
| 23 | A01-016 | A | living-room-plate、metallica_official_02、metallica_official_01 | 69.5 足をかけた本の山が崩れ、体が前につんのめる→70.3「う、」で両手をテーブルについて踏みとどまる→直後の0.9秒の間で跳ね起きる。顔は真っ赤 |
| 24 | A01-017 | M | living-room-plate、metallica_official_02、metallica_official_01 | 腕を振り回して否定する |
| 25 | A01-017a | K | living-room-plate、metallica_official_02、metallica_official_01 | 腕を組んであごを上げ、そっぽを向く |
| 26 | A01-017b | M | living-room-plate、metallica_official_02、metallica_official_01 | 「浮かれるものか！」で帽子のつばをぐいと引き下ろす |
| 27 | A01-018 | M | living-room-plate、metallica_official_02、metallica_official_01 | 背を向けて丸窓へ。招待状を丸窓から差し込む朝の光にかざす。透かし模様。口元が勝手にゆるむ |
| 28 | A01-019 | K | living-room-plate、bisco_official_03、bisco_official_01、metallica_official_02、metallica_official_01 | メタリカの背中を見ながら |
| 29 | A01-019a | M | living-room-plate、metallica_official_02、metallica_official_01 | （声はオフ）肩がわずかに上がる。口元は見せない |
| 30 | A01-020 | M | living-room-plate、arlecchino_official_03 | ポットを取り、メタリカのカップに注ぎ始める |
| 31 | A01-021 | M | living-room-plate | （声はオフ）「まさに」（97.6）で縁まで来ても、注ぎ続ける |
| 32 | A01-022 | M | living-room-plate、arlecchino_official_03、bisco_official_03、bisco_official_01 | あふれた紅茶が赤い布に広がり、ビスコの方へ流れる。ビスコは顔も上げず、自分の皿だけをすっと持ち上げる |
| 33 | A01-023 | S | living-room-plate、metallica_official_02、metallica_official_01 | （声はオフ）「リカ様」（101.2）で背中が止まる。怒りマークを「リカ様」「成就」（102.4）「はい」（104.6）の3回で一つずつ増やす |
| 34 | A01-024 | A | living-room-plate、metallica_official_02、metallica_official_01 | 髪と帽子のつばで円を描いて振り返り、「呼べ！」（108.5）で睨みつける |
| 35 | A01-025 | A | living-room-plate、metallica_official_02、metallica_official_01、arlecchino_official_03 | L14 の息継ぎを広げた間に部屋を大股3歩で横切る→「それに、」（109.8）でアルレッキーノの鼻先に指を突きつけ、顔を寄せて怒鳴る。アルレッキーノはポットを持ったまま微笑みを崩さない |
| 36 | A01-026 | M | living-room-plate、arlecchino_official_03 | 悪びれず一礼し、紅茶の染みた布を拭き始める |
| 37 | A01-027 | M | living-room-plate、metallica_official_02、metallica_official_01 | ふんと背を向け、腕を振って丸窓の方へ戻りながら |
| 38 | A01-028 | M | living-room-plate、metallica_official_02、metallica_official_01 | 「いや待て」で歩きが止まり、直後の0.9秒の間であごに指を当て、目を細める |
| 39 | A01-029 | K | witch-house-exterior_ingame_01、swamp_ingame_02 | （声はオフ）翠の水面から瘴気が立ちのぼり、ゆらぐ |
| 40 | A01-030 | M | living-room-plate、metallica_official_02、metallica_official_01 | 「フム」のあとの1.1秒の間で、あごに当てた指をとんとんと動かす。本気で考え込む |
| 41 | A01-030a | K | living-room-plate | （声はオフ）赤いソファに座る人形へ、ゆっくり寄る |
| 42 | A01-031 | K | living-room-plate、arlecchino_official_03、bisco_official_03、bisco_official_01、hundred-knight_official_02 | アルレッキーノは布を拭き、百騎兵はパンで黄身をぬぐい、ビスコは紅茶をすする |
| 43 | A01-032 | M | living-room-plate、metallica_official_02、metallica_official_01 | ソファの肘掛けに腰を下ろし、あふれた紅茶に顔をしかめ、それでも飲む。「それにしても、」のあとの0.7秒の間でカップを下ろし、ビスコを見る |
| 44 | A01-032a | K | living-room-plate、metallica_official_02、metallica_official_01 | 横目でビスコを見る |
| 45 | A01-033 | K | living-room-plate、bisco_official_03、bisco_official_01 | 目をパンに落とす |
| 46 | A01-033a | M | living-room-plate、bisco_official_03、bisco_official_01 | （声はオフ）ためらいの0.5秒でパンをちぎる手が止まり、「王宮付き」でまた動き出す |
| 47 | A01-034 | K | living-room-plate、bisco_official_03、bisco_official_01 | 顔を上げ、さらりとした顔に戻る。「知っているさ」で小さく肩をすくめる |
| 48 | A01-035 | K | living-room-plate、metallica_official_02、metallica_official_01 | （声はオフ）「孤独を常とする」で、一瞬だけ丸窓へ目をやる |
| 49 | A01-036 | M | living-room-plate、metallica_official_02、metallica_official_01 | 「そうだ。」で視線を戻し、直後の0.8秒の間で遠くを見る目になる。そのまま少しやわらいだ顔で語る |
| 50 | A01-037 | K | living-room-plate、bisco_official_03、bisco_official_01 | 少し意外そうに、口元がゆるむ |
| 51 | A01-038 | M | living-room-plate、metallica_official_02、metallica_official_01 | 「敬意？」でまばたき。直後の0.9秒の間で、空っぽの顔からにやりとした顔に変わる |
| 52 | A01-038a | A | living-room-plate、metallica_official_02、metallica_official_01 | 「キヒヒ！」でソファに体ごと倒れ込み、ブーツをテーブルに叩きつける（積んだ本が跳ねる）。隣の人形も弾む |
| 53 | A01-038b | K | living-room-plate、metallica_official_02、metallica_official_01 | ブーツをテーブルに乗せたまま、にやり |
| 54 | A01-039 | K | living-room-plate、bisco_official_03、bisco_official_01 | 身を乗り出す。眉が寄る |
| 55 | A01-040 | A | living-room-plate、metallica_official_02、metallica_official_01 | 「心配するな。」でブーツを下ろしてソファから跳ね起き、直後の0.9秒の間で帽子をかぶり直す |
| 56 | A01-040a | A | living-room-plate、metallica_official_02、metallica_official_01 | マントを翻して玄関へ大股で歩き、「重要」（198.5）で両手で扉を開け放つ。翠の光が部屋に流れ込み、メタリカが影になる |
