#!/usr/bin/env python3
"""
data/comments.jsonl を読んで、依頼された5つの抽出軸に機械的に仕分ける。

    python3 tools/analyze_comments.py            # 集計とサンプルを標準出力へ
    python3 tools/analyze_comments.py --dump     # data/digest/ に軸ごとの全文を書き出す

投稿者本人（isUploader）の返信は視聴者の声から除外し、
「発信者が何に答えているか」の材料として別に数える。
"""
import argparse, json, re, sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

AXES = {
    # 1. 未充足ニーズ＝質問。誰も動画で答えていない可能性がある
    "question": [
        r"[?？]\s*$", r"ですか[?？]", r"でしょうか", r"教えて", r"知りたい",
        r"どう(やって|すれば|したら)", r"どこで", r"何を", r"いくら", r"可能ですか",
        r"アドバイス", r"聞きたい", r"質問",
    ],
    # 2. 不信感の言語
    "distrust": [
        # 「本当に」「実績」「スクール」単体は称賛や中立の質問まで拾うので入れない
        r"稼げな", r"騙", r"詐欺", r"うさん", r"胡散", r"怪しい", r"嘘", r"ウソ",
        r"ネズミ講", r"情報商材", r"養分", r"カモ", r"煽", r"転売ヤー", r"やらせ",
        r"仕込み", r"盛って", r"眉唾", r"信用でき", r"信じられ", r"信憑",
        r"本当ですか", r"本当なの", r"ほんとに稼", r"本当に稼", r"証拠", r"スクショ",
        r"いくらかかる", r"お高いん", r"高額な?(コンサル|スクール|塾|講座)",
        r"(スクール|コンサル|塾|講座).{0,10}(いくら|値段|料金|費用|金額|高い)",
        r"勧誘", r"回し者", r"ステマ",
    ],
    # 3. 挫折・離脱の詰まりどころ
    "stuck": [
        r"できない", r"出来ない", r"見つから", r"売れない", r"仕入れられ", r"挫折",
        r"やめ(た|ようか|ます)", r"続かな", r"しんどい", r"つらい", r"辛い", r"心が折",
        r"不安", r"怖い", r"難しい", r"わからな", r"分からな", r"迷", r"自信がな",
        r"赤字", r"在庫", r"売れ残",
    ],
    # 4. ポジショニング検証（棚読み仮説の裏付けと反証）
    "positioning": [
        r"リスト", r"利益商品", r"リサーチ", r"目利き", r"見極め", r"判断", r"基準",
        r"相場", r"棚", r"品出し", r"回転", r"値付け", r"なぜ", r"理由", r"ロジック",
        r"考え方", r"Keepa", r"せどりすと", r"アプリ",
    ],
    # 5. 属性と語彙
    "profile": [
        r"\d+代", r"歳", r"主婦", r"ママ", r"パパ", r"子ども", r"子供", r"育児",
        r"本業", r"副業", r"サラリーマン", r"会社員", r"会社辞", r"退職", r"定年",
        r"年金", r"独身", r"夫", r"妻", r"旦那", r"嫁", r"地方", r"田舎", r"都会",
        r"車", r"免許", r"資金", r"軍資金", r"貯金", r"万円から", r"初心者", r"始めて",
        r"始めた", r"転売", r"古物商",
    ],
}
COMPILED = {k: [re.compile(p) for p in v] for k, v in AXES.items()}


def classify(text):
    hits = []
    for axis, pats in COMPILED.items():
        if any(p.search(text) for p in pats):
            hits.append(axis)
    return hits


def load():
    rows = []
    path = DATA / "comments.jsonl"
    if not path.exists():
        sys.exit(f"{path} がありません。--stage comments を先に実行してください。")
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def flatten(videos):
    """1コメント1行に開く。返信は親の文脈を持たせる。"""
    out = []
    for v in videos:
        meta = {k: v[k] for k in ("videoId", "title", "channelTitle", "segment", "views")}
        cid = v.get("channelId")

        def is_host(c):
            # API版は投稿者のチャンネルIDで、yt-dlp版はフラグで判定できる
            if c.get("authorChannelId"):
                return c["authorChannelId"] == cid
            return bool(c.get("isUploader"))

        for t in v.get("threads", []):
            out.append({**meta, "kind": "top", "parentText": None,
                        "author": t["author"], "text": t["text"],
                        "likes": t["likes"], "isUploader": is_host(t)})
            for r in t.get("replies", []):
                out.append({**meta, "kind": "reply", "parentText": t["text"],
                            "author": r["author"], "text": r["text"],
                            "likes": r["likes"], "isUploader": is_host(r)})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", action="store_true")
    ap.add_argument("--samples", type=int, default=15)
    a = ap.parse_args()

    videos = load()
    rows = flatten(videos)
    viewers = [r for r in rows if not r["isUploader"]]
    hosts = [r for r in rows if r["isUploader"]]

    print(f"videos={len(videos)}  comments={len(rows)}  "
          f"viewer={len(viewers)}  uploader={len(hosts)}")
    print(f"channels={len(set(r['channelTitle'] for r in rows))}")
    print()

    buckets = defaultdict(list)
    for r in viewers:
        for axis in classify(r["text"]):
            buckets[axis].append(r)
    for axis in AXES:
        n = len(buckets[axis])
        print(f"  {axis:<12} {n:>5}  ({n/max(len(viewers),1)*100:.1f}%)")
    print()

    seg = Counter(r["segment"] for r in viewers)
    print("segment:", dict(seg))
    print()

    for axis in AXES:
        rows_a = sorted(buckets[axis], key=lambda x: -x["likes"])[:a.samples]
        print(f"--- {axis} (top {len(rows_a)} by likes) ---")
        for r in rows_a:
            txt = " ".join(r["text"].split())[:150]
            print(f"  [{r['likes']:>3}👍 {r['channelTitle'][:12]}] {txt}")
        print()

    if a.dump:
        out = DATA / "digest"
        out.mkdir(exist_ok=True)
        for axis in AXES:
            lines = []
            for r in sorted(buckets[axis], key=lambda x: -x["likes"]):
                txt = " ".join(r["text"].split())
                ctx = (" ／親: " + " ".join(r["parentText"].split())[:80]) if r["parentText"] else ""
                lines.append(f"[{r['likes']}👍|{r['channelTitle']}|{r['title'][:30]}] {txt}{ctx}")
            (out / f"{axis}.txt").write_text("\n".join(lines), encoding="utf-8")
        # 軸に当たらなかったものも残す（取りこぼしの確認用）
        rest = [r for r in viewers if not classify(r["text"])]
        (out / "unclassified.txt").write_text(
            "\n".join(f"[{r['likes']}👍|{r['channelTitle']}] " + " ".join(r["text"].split())
                      for r in sorted(rest, key=lambda x: -x["likes"])), encoding="utf-8")
        print(f"dumped -> {out}  (unclassified={len(rest)})")


if __name__ == "__main__":
    main()
