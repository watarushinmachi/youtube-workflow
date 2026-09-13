#!/usr/bin/env python3
"""
中古（リサイクルショップ→メルカリ）系だけを厚く集める追加収集。

docs/segment-split.md で中古の母数が209件しかなく、とくに「仕入れ先の枯渇」が
7件しか取れていないため、中古専門チャンネルに絞って取り直す。

    python3 tools/fetch_used.py --stage videos
    python3 tools/fetch_used.py --stage comments
出力は data/used_videos.json / data/used_comments.jsonl
"""
import argparse, json, subprocess, sys, time, re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data"; OUT.mkdir(exist_ok=True)

# 中古1点物をフリマで売る型だけ。うみぞう・こうじ・名人・船原・作間（新品）は入れない
USED_CH = {
    "UCGqIjerlolb_ELMWuPXIN5A": "トレハン部 -お宝発掘大作戦-",
    "UCaQpqlrKICzBnSecai6NuEw": "毎日学べるせどり大学 〜アパリセ〜",
    "UCbb2ADJb5C9kCPea5Gik-JQ": "ケンスケ【中古せどりYouTube】",
    "UCCr7KBbQxmTR3U5U39xFtTw": "しゅん@即売れ中古せどり",
    "UCZDpWSjclZeoXDhz9LPMJAA": "hito*メルカリ在宅ワークで家建てたママ",
    "UCbNZxUa2_nv5LTNdIPi4Kzw": "在宅メルカリママ [さき]",
    "UC6pdfYjHPP9irmmIOwOvgMg": "えだまめの車中泊せどり生活",
    "UC2F3mZz6JQdrPeWXpe0QKdg": "メルカリで稼ぐブランド物販 福井ゆかり",
}
# 中古の現場語。新品系（ドンキ・家電・FBA専業）の回は落とす
USED_HINT = re.compile(r"セカスト|セカンドストリート|オフハウス|ハードオフ|ブックオフ|トレファク|"
                       r"トレジャーファクトリー|リサイクル|古着|中古|アパレル|バッグ|腕時計|時計|"
                       r"食器|雑貨|ブランド|メルカリ|フリマ|仕入れ|せどり")
DROP = re.compile(r"ドンキ|家電|Amazonせどり|FBA納品|電脳|中国輸入|ポケモンカード|新品")


def ytdlp(args, timeout=600):
    p = subprocess.run(["yt-dlp", "--no-warnings", *args], capture_output=True, text=True, timeout=timeout)
    return p.stdout, p.stderr, p.returncode


def stage_videos(args):
    rows = []
    for cid, name in USED_CH.items():
        out, err, rc = ytdlp(["--flat-playlist", "-J", "--playlist-end", str(args.scan),
                              f"https://www.youtube.com/channel/{cid}/videos"])
        if rc != 0 or not out.strip():
            print(f"  !! {name}: {err.strip()[:120]}", file=sys.stderr); continue
        d = json.loads(out)
        n = 0
        for e in d.get("entries", []):
            t = e.get("title") or ""
            if int(e.get("duration") or 0) < 480: continue
            if not USED_HINT.search(t) or DROP.search(t): continue
            rows.append({"videoId": e["id"], "title": t, "channelId": cid,
                         "channelTitle": d.get("channel") or name,
                         "durationSec": int(e.get("duration") or 0)})
            n += 1
        print(f"  {name[:28]:<28} {n:>3}本")
        time.sleep(args.delay)
    (OUT / "used_candidates.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"候補 {len(rows)}本 -> used_candidates.json")

    # 再生数を取って、チャンネルごとに上位を選ぶ
    cache_p = OUT / "used_meta.json"
    cache = json.loads(cache_p.read_text(encoding="utf-8")) if cache_p.exists() else {}
    by = {}
    for r in rows: by.setdefault(r["channelId"], []).append(r)
    targets = []
    for cid, rs in by.items(): targets += rs[:args.meta_per_channel]
    todo = [r for r in targets if r["videoId"] not in cache]
    print(f"再生数を取る: {len(todo)}本")
    fails = 0
    for i, r in enumerate(todo, 1):
        o, e, rc = ytdlp(["-J", "--skip-download", f"https://www.youtube.com/watch?v={r['videoId']}"], timeout=120)
        if rc == 0 and o.strip():
            d = json.loads(o)
            cache[r["videoId"]] = {"views": d.get("view_count") or 0,
                                   "comments": d.get("comment_count") or 0,
                                   "likes": d.get("like_count") or 0}
            fails = 0
        else:
            fails += 1
            if fails >= 3:
                print(f"  .. 連続失敗。{args.cooldown}秒待つ"); time.sleep(args.cooldown); fails = 0
        time.sleep(args.delay)
        if i % 20 == 0:
            cache_p.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            print(f"  [{i}/{len(todo)}]", flush=True)
    cache_p.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

    cand = [{**r, **cache[r["videoId"]]} for r in targets
            if r["videoId"] in cache and cache[r["videoId"]]["comments"] >= args.min_comments]
    pick, seen = [], {}
    for r in sorted(cand, key=lambda x: -x["views"]):
        c = seen.get(r["channelId"], 0)
        if c >= args.per_channel: continue
        seen[r["channelId"]] = c + 1; pick.append(r)
        if len(pick) >= args.n: break
    (OUT / "used_videos.json").write_text(json.dumps(pick, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n選定 {len(pick)}本")
    for v in pick:
        print(f"  {v['views']:>8,}v {v['comments']:>4}c  {v['channelTitle'][:16]:<16} {v['title'][:44]}")


def fetch_threads(vid, max_top, max_replies):
    ea = f"youtube:comment_sort=top;max_comments={max_top*(max_replies+1)},all,{max_top},{max_replies}"
    o, e, rc = ytdlp(["-J", "--skip-download", "--write-comments", "--extractor-args", ea,
                      f"https://www.youtube.com/watch?v={vid}"], timeout=900)
    if rc != 0 or not o.strip():
        print(f"  !! {vid}: {e.strip()[:120]}", file=sys.stderr); return []
    flat = (json.loads(o).get("comments") or [])
    tops, kids = {}, {}
    for c in flat:
        row = {"author": c.get("author") or "", "text": c.get("text") or "",
               "likes": c.get("like_count") or 0, "isUploader": bool(c.get("author_is_uploader"))}
        if c.get("parent") == "root": tops[c["id"]] = {**row, "replies": []}
        else: kids.setdefault(c.get("parent"), []).append(row)
    for pid, reps in kids.items():
        if pid in tops: tops[pid]["replies"] = reps
    return list(tops.values())


def stage_comments(args):
    sel = json.loads((OUT / "used_videos.json").read_text(encoding="utf-8"))
    path = OUT / "used_comments.jsonl"
    done = set()
    if path.exists() and not args.overwrite:
        for l in path.read_text(encoding="utf-8").splitlines():
            if l.strip(): done.add(json.loads(l)["videoId"])
    with path.open("w" if args.overwrite else "a", encoding="utf-8") as f:
        for i, v in enumerate(sel, 1):
            if v["videoId"] in done:
                print(f"  [{i}/{len(sel)}] skip"); continue
            th = fetch_threads(v["videoId"], args.max_top, args.max_replies)
            f.write(json.dumps({**v, "threads": th}, ensure_ascii=False) + "\n"); f.flush()
            print(f"  [{i}/{len(sel)}] {len(th):>4} threads  {v['title'][:40]}", flush=True)
            time.sleep(args.delay)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--stage", choices=["videos", "comments"], required=True)
    p.add_argument("--scan", type=int, default=200)
    p.add_argument("--meta-per-channel", type=int, default=22)
    p.add_argument("--per-channel", type=int, default=5)
    p.add_argument("--n", type=int, default=30)
    p.add_argument("--min-comments", type=int, default=5)
    p.add_argument("--max-top", type=int, default=400)
    p.add_argument("--max-replies", type=int, default=20)
    p.add_argument("--delay", type=float, default=2.5)
    p.add_argument("--cooldown", type=int, default=300)
    p.add_argument("--overwrite", action="store_true")
    a = p.parse_args()
    (stage_videos if a.stage == "videos" else stage_comments)(a)
