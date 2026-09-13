#!/usr/bin/env python3
"""
物販・せどり界隈の上位動画とそのコメントを yt-dlp で収集する（APIキー不要版）。

fetch_comments.py と同じ出力形式（data/candidates.json, data/selected_videos.json,
data/comments.jsonl）を作る。YouTube Data API v3 のキーが用意できない環境用。

使い方:
    python3 tools/fetch_comments_ytdlp.py --stage scan      # 全チャンネルの動画一覧
    python3 tools/fetch_comments_ytdlp.py --stage videos    # 再生数を取って選定
    python3 tools/fetch_comments_ytdlp.py --stage comments  # コメント全取得

scan と videos を分けているのは、再生数の取得が1本1リクエストで時間がかかるため。
scan の結果（data/scan.json）は再利用できる。
"""
import argparse, json, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data"
OUT.mkdir(exist_ok=True)

sys.path.insert(0, str(ROOT / "tools"))
# チャンネル定義は API 版と共有する（USED / NEW / SOURCING_HINTS）
import importlib.util
_spec = importlib.util.spec_from_file_location("_api_ver", ROOT / "tools" / "fetch_comments.py")
_m = importlib.util.module_from_spec(_spec)
_m.__dict__["__name__"] = "_api_ver"      # __main__ ガードを回避
try:
    _spec.loader.exec_module(_m)
except SystemExit:
    pass
USED, NEW, SOURCING_HINTS = _m.USED, _m.NEW, _m.SOURCING_HINTS


def ytdlp(args, timeout=300):
    p = subprocess.run(["yt-dlp", "--no-warnings", *args],
                       capture_output=True, text=True, timeout=timeout)
    return p.stdout, p.stderr, p.returncode


def scan_channel(cid, cap):
    """flat-playlist で id / title / duration だけ取る（速い）"""
    out, err, rc = ytdlp(["--flat-playlist", "-J", "--playlist-end", str(cap),
                          f"https://www.youtube.com/channel/{cid}/videos"])
    if rc != 0 or not out.strip():
        print(f"  !! scan failed {cid}: {err.strip()[:160]}", file=sys.stderr)
        return []
    d = json.loads(out)
    rows = []
    for e in d.get("entries", []):
        if not e.get("id"):
            continue
        rows.append({
            "videoId": e["id"],
            "title": e.get("title") or "",
            "durationSec": int(e.get("duration") or 0),
            "channelId": cid,
            "channelTitle": d.get("channel") or d.get("uploader") or cid,
        })
    return rows


def stage_scan(args):
    pool = {**USED, **NEW}
    all_rows = []
    for i, (cid, name) in enumerate(pool.items(), 1):
        t0 = time.time()
        rows = scan_channel(cid, args.scan_per_channel)
        for r in rows:
            r["segment"] = "used" if cid in USED else "new"
        all_rows += rows
        print(f"  [{i}/{len(pool)}] {name[:30]:<30} {len(rows):>4} videos  ({time.time()-t0:.0f}s)")
    (OUT / "scan.json").write_text(json.dumps(all_rows, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    print(f"saved -> {OUT/'scan.json'}  ({len(all_rows)} videos)")


def video_meta(vid):
    out, err, rc = ytdlp(["-J", "--skip-download", f"https://www.youtube.com/watch?v={vid}"],
                         timeout=120)
    if rc != 0 or not out.strip():
        return None
    d = json.loads(out)
    return {
        "views": d.get("view_count") or 0,
        "likes": d.get("like_count") or 0,
        "comments": d.get("comment_count") or 0,
        "publishedAt": d.get("upload_date") or "",
    }


def stage_videos(args):
    scan = json.loads((OUT / "scan.json").read_text(encoding="utf-8"))

    def sourcing(v):
        return (v["durationSec"] >= 480
                and any(h in v["title"] for h in SOURCING_HINTS))

    pre = [v for v in scan if sourcing(v)]
    # チャンネルごとに新しい順で上位 N本だけ再生数を取る（全部取ると時間がかかりすぎる）
    by_ch = {}
    for v in pre:
        by_ch.setdefault(v["channelId"], []).append(v)
    targets = []
    for cid, rows in by_ch.items():
        targets += rows[:args.meta_per_channel]
    print(f"filter: {len(scan)} -> {len(pre)} sourcing videos -> "
          f"{len(targets)} to fetch metadata")

    cache_path = OUT / "meta_cache.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    todo = [v["videoId"] for v in targets if v["videoId"] not in cache]
    print(f"metadata: {len(todo)} to fetch ({len(cache)} cached)")
    fails = 0
    for i, vid in enumerate(todo, 1):
        m = video_meta(vid)
        if m:
            cache[vid] = m
            fails = 0
        else:
            fails += 1
            # 連続で落ちるのは bot 判定。冷ますまで待つ
            if fails >= 3:
                print(f"  .. {fails} consecutive failures, sleeping {args.cooldown}s")
                time.sleep(args.cooldown)
                fails = 0
        time.sleep(args.delay)
        if i % 25 == 0:
            cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            print(f"  [{i}/{len(todo)}] cached {len(cache)}", flush=True)
    cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

    cand = []
    for v in targets:
        m = cache.get(v["videoId"])
        if not m:
            continue
        if m["comments"] < args.min_comments:
            continue
        cand.append({**v, **m})

    used = sorted([v for v in cand if v["segment"] == "used"], key=lambda x: -x["views"])
    new = sorted([v for v in cand if v["segment"] == "new"], key=lambda x: -x["views"])

    def diversify(rows, per_channel, limit):
        seen, out = {}, []
        for r in rows:
            c = seen.get(r["channelId"], 0)
            if c >= per_channel:
                continue
            seen[r["channelId"]] = c + 1
            out.append(r)
            if len(out) >= limit:
                break
        return out

    pick = diversify(used, args.per_channel, args.n_used) + \
           diversify(new, args.per_channel, args.n_new)

    (OUT / "candidates.json").write_text(json.dumps(cand, ensure_ascii=False, indent=2),
                                         encoding="utf-8")
    (OUT / "selected_videos.json").write_text(json.dumps(pick, ensure_ascii=False, indent=2),
                                              encoding="utf-8")
    print(f"candidates={len(cand)}  selected={len(pick)}")
    for v in pick:
        print(f"  [{v['segment']}] {v['views']:>8,}v {v['comments']:>4}c  "
              f"{v['channelTitle'][:16]:<16} {v['title'][:46]}")


def fetch_threads(vid, max_top, max_replies):
    """返信込みで取得し、API版と同じ入れ子の形に組み直す"""
    ea = f"youtube:comment_sort=top;max_comments={max_top*(max_replies+1)},all,{max_top},{max_replies}"
    out, err, rc = ytdlp(["-J", "--skip-download", "--write-comments",
                          "--extractor-args", ea,
                          f"https://www.youtube.com/watch?v={vid}"], timeout=900)
    if rc != 0 or not out.strip():
        print(f"  !! comments failed {vid}: {err.strip()[:160]}", file=sys.stderr)
        return []
    d = json.loads(out)
    flat = d.get("comments") or []
    tops, kids = {}, {}
    for c in flat:
        row = {
            "author": c.get("author") or "",
            "text": c.get("text") or "",
            "likes": c.get("like_count") or 0,
            "publishedAt": c.get("timestamp"),
            "isUploader": bool(c.get("author_is_uploader")),
        }
        if c.get("parent") == "root":
            tops[c["id"]] = {**row, "replyCount": 0, "replies": []}
        else:
            kids.setdefault(c.get("parent"), []).append(row)
    for pid, reps in kids.items():
        if pid in tops:
            tops[pid]["replies"] = reps
            tops[pid]["replyCount"] = len(reps)
    return list(tops.values())


def stage_comments(args):
    sel = json.loads((OUT / "selected_videos.json").read_text(encoding="utf-8"))
    out_path = OUT / "comments.jsonl"
    done = set()
    if out_path.exists() and not args.overwrite:
        for line in out_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                done.add(json.loads(line)["videoId"])
    mode = "w" if args.overwrite else "a"
    total = 0
    with out_path.open(mode, encoding="utf-8") as f:
        for i, v in enumerate(sel, 1):
            if v["videoId"] in done:
                print(f"  [{i}/{len(sel)}] skip (done) {v['title'][:40]}")
                continue
            th = fetch_threads(v["videoId"], args.max_top, args.max_replies)
            time.sleep(args.delay)
            total += len(th)
            f.write(json.dumps({**v, "threads": th}, ensure_ascii=False) + "\n")
            f.flush()
            print(f"  [{i}/{len(sel)}] {len(th):>4} threads  {v['title'][:40]}")
    print(f"saved -> {out_path}  (+{total} threads this run)")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--stage", choices=["scan", "videos", "comments"], required=True)
    p.add_argument("--scan-per-channel", type=int, default=200)
    p.add_argument("--meta-per-channel", type=int, default=25)
    p.add_argument("--per-channel", type=int, default=3)
    p.add_argument("--n-used", type=int, default=20)
    p.add_argument("--n-new", type=int, default=10)
    p.add_argument("--min-comments", type=int, default=5)
    p.add_argument("--max-top", type=int, default=400)
    p.add_argument("--max-replies", type=int, default=20)
    p.add_argument("--delay", type=float, default=2.0)
    p.add_argument("--cooldown", type=int, default=300)
    p.add_argument("--overwrite", action="store_true")
    a = p.parse_args()
    {"scan": stage_scan, "videos": stage_videos, "comments": stage_comments}[a.stage](a)
