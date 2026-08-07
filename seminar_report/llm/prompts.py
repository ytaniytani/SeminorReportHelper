"""レポート生成用プロンプト。

キャプチャ位置の指定は本文中の `[[capture:HH:MM:SS|キャプション]]` マーカーで行う。
LLM が時刻を作話すると画像がずれるため、「入力に実在する時刻だけを使う」ことを
繰り返し明示している(生成後にも markers.py 側で機械的に検証する)。
"""

from __future__ import annotations

SYSTEM = """あなたは技術セミナーの内容を正確に理解し、社内共有用のレポートに\
まとめる専門のテクニカルライターです。

原則:
- 文字起こしに書かれていないことを推測で事実として書かない。
- 一方で、単なる発言の羅列にはしない。論点の構造・因果・結論を読み取り、
  「何が重要だったか」を明示する。
- 冗長な言い回し、フィラー、言い直しは整理して読みやすい文章にする。
- 文字起こしの誤変換は文脈から明らかな場合のみ正す。
"""

CAPTURE_RULES = """
## 画像キャプチャの指定方法

本文中で「図・スライド・デモ画面を見せた方が読者の理解が進む」と判断した箇所に、
次の形式のマーカーを単独の行として挿入してください。

[[capture:HH:MM:SS|画像のキャプション]]

厳守事項:
- 時刻は **入力の文字起こしに実際に現れる `[HH:MM:SS]` の値をそのまま使う**こと。
  自分で時刻を計算・推測して書かないこと。
- その内容について話している発言の時刻を指定すること。
- キャプションは画像が何を示しているかが分かる簡潔な日本語にすること。
- マーカーは必ず行頭から始め、1 行に 1 つだけ書くこと。
- このセクションには **必ず 1 個以上、最大 {max_captures} 個**のマーカーを入れること。
  一見スライドが無さそうな内容でも、そのセクションの内容を最もよく象徴する
  瞬間（登壇者が要点を語っている場面など）を 1 つ選んで指定すること。
  0 個にはしないこと。
"""


def chunk_summary_prompt(timestamped_text: str, index: int, total: int) -> str:
    """map 段: 動画の一部分から要点を抽出する。"""
    return f"""以下はセミナー動画の文字起こしの一部です(全 {total} 分割中の {index + 1} 番目)。
各行の先頭 `[HH:MM:SS]` は動画内の時刻です。

この部分で語られた内容を、後段でレポートの章立てを設計するための材料として\
まとめてください。

出力は次の JSON のみ(説明文やコードフェンスは不要):
{{
  "summary": "この区間の内容の要約。300〜500字程度。何が論じられ、何が結論だったか。",
  "topics": [
    "この区間で扱われた主要トピックを、時刻付きで簡潔に。例: '[00:12:30] 導入事例の紹介'"
  ]
}}

--- 文字起こし ---
{timestamped_text}
"""


def outline_prompt(
    summaries_text: str,
    duration_label: str,
    target_chars: int,
    section_range: tuple[int, int],
    language: str,
) -> str:
    """outline 段: 内容に応じた章立てを AI 自身に決めさせる。"""
    low, high = section_range
    return f"""以下は、長さ {duration_label} のセミナー動画を区間ごとに要約したものです。

これ全体を踏まえて、{language}のレポートの構成を設計してください。
決まった雛形に当てはめるのではなく、**この動画の内容にとって最も自然で\
読み手に伝わる章立て**を考えてください。

条件:
- セクション数は {low}〜{high} 個。
- レポート本文全体の目標文字数は約 {target_chars} 字。この分量に収まる粒度で構成すること。
- 各セクションには、そのセクションが対応する動画内の時刻範囲を指定すること。
  時刻範囲は入力の要約に現れる時刻に基づくこと。
- セクションは時系列順に並べること。

出力は次の JSON のみ(説明文やコードフェンスは不要):
{{
  "title": "レポートのタイトル。セミナーの主題が分かるもの",
  "key_points": [
    "このセミナーの要点を3〜6個。各1〜2文。読者がここだけ読んでも要旨が掴めること"
  ],
  "sections": [
    {{
      "title": "セクション見出し",
      "start": "HH:MM:SS",
      "end": "HH:MM:SS",
      "focus": "このセクションで何を書くべきかの指示。1〜2文"
    }}
  ]
}}

--- 区間要約 ---
{summaries_text}
"""


def section_write_prompt(
    section_title: str,
    focus: str,
    timestamped_text: str,
    target_chars: int,
    max_captures: int,
    language: str,
    context_note: str = "",
) -> str:
    """write 段: セクション本文を書く。ここでキャプチャ位置も決まる。"""
    capture_rules = CAPTURE_RULES.format(max_captures=max_captures)
    context_block = f"\n## 前のセクションまでの流れ\n{context_note}\n" if context_note else ""

    return f"""セミナーレポートの 1 セクションを{language}で執筆してください。

## セクション
見出し: {section_title}
書くべき内容: {focus}
目標文字数: 約 {target_chars} 字
{context_block}
## 執筆方針
- 見出し行(`## ...`)は出力に含めないこと。本文だけを書くこと。
- 発言の書き起こしではなく、要点を再構成した読みやすい文章にすること。
- 重要な数値・固有名詞・結論は落とさないこと。
- 箇条書きが有効な箇所では箇条書きを使ってよい(Markdown)。
- 登壇者の主張と、事実・データを区別して書くこと。
{capture_rules}
--- 該当区間の文字起こし ---
{timestamped_text}
"""


def overview_prompt(
    title: str, key_points_text: str, sections_text: str, language: str, target_chars: int
) -> str:
    """レポート冒頭の概要を書く。"""
    return f"""次のセミナーレポートの冒頭に置く「概要」を{language}で書いてください。

タイトル: {title}

要点:
{key_points_text}

章立て:
{sections_text}

条件:
- 約 {target_chars} 字。
- このレポートを開いた人が、読むべきかどうかを判断できる内容にすること。
- 見出しは付けず、本文だけを出力すること。
- 箇条書きにせず、文章で書くこと。
"""


def rewrite_length_prompt(body: str, target_chars: int, current_chars: int, language: str) -> str:
    """文字数が目標から大きく外れたときの 1 回だけの修正。"""
    direction = "短く要約し直して" if current_chars > target_chars else "内容を補って厚く"
    return f"""次の{language}の文章は現在 {current_chars} 字ですが、目標は約 {target_chars} 字です。
{direction}ください。

厳守事項:
- `[[capture:...]]` の行は **一字一句そのまま**残すこと。削除も改変もしないこと。
- 事実を追加で捏造しないこと。分量を増やす場合は既に書かれている内容の説明を丁寧にすること。
- 本文だけを出力すること。

--- 対象の文章 ---
{body}
"""


CAPTION_VERIFY_PROMPT = """この画像はセミナー動画から切り出したものです。
レポートには「{caption}」という説明で載せる予定です。

次の JSON のみを出力してください(説明文不要):
{{
  "useful": true または false,
  "caption": "画像の実際の内容に即した、より適切な日本語キャプション(30字以内)"
}}

useful の判定基準:
- スライド・図表・デモ画面・コードなど、読者にとって情報がある画像なら true。
- 話者の顔だけ、真っ暗、切り替わり途中でぼやけている、意味のある情報が無いなら false。
"""
