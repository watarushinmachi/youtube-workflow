#!/usr/bin/env python3
"""
物販・せどり界隈の上位動画とそのコメントを YouTube Data API v3 で収集する。

使い方:
    export YOUTUBE_API_KEY=xxxx
    python3 tools/fetch_comments.py --stage videos    # 候補動画を集めてランキング
    python3 tools/fetch_comments.py --stage comments  # 選定済み動画のコメントを全取得

クォータ目安（無料枠 10,000/日）:
    channels.list        1 / 50ch
    playlistItems.list   1 / 50本
    videos.list          1 / 50本
    commentThreads.list  1 / 100スレッド
  → 30本フル取得しても概ね 500 未満。
"""
import argparse, json, os, sys, time
from pathlib import Path
import requests

API = "https://www.googleapis.com/youtube/v3"
KEY = os.environ.get("YOUTUBE_API_KEY", "")
OUT = Path(__file__).resolve().parent.parent / "data"
OUT.mkdir(exist_ok=True)

# 中古（リユース店舗仕入れ）系
USED = {
    "UCGqIjerlolb_ELMWuPXIN5A": "トレハン部 -お宝発掘大作戦-",
    "UCaQpqlrKICzBnSecai6NuEw": "毎日学べるせどり大学 〜アパリセ〜",
    "UCoQfb6HiGLvBeH28M-d2C6w": "うみぞうの非常識な店舗せどり攻略",
    "UCbb2ADJb5C9kCPea5Gik-JQ": "ケンスケ【中古せどりYouTube】",
    "UCDBlHIHenvD9NQ6LtKBVcxA": "【セカプロ】卸せどりch",
    "UC8FQbndTCXmucaMwY4egFzA": "いとう社長@脱店舗せどりの新手法",
    "UCCr7KBbQxmTR3U5U39xFtTw": "しゅん@即売れ中古せどり",
    "UCZDpWSjclZeoXDhz9LPMJAA": "hito*メルカリ在宅ワークで家建てたママ",
    "UCbNZxUa2_nv5LTNdIPi4Kzw": "在宅メルカリママ [さき]",
    "UC6pdfYjHPP9irmmIOwOvgMg": "えだまめの車中泊せどり生活",
    "UC2F3mZz6JQdrPeWXpe0QKdg": "メルカリで稼ぐブランド物販 福井ゆかり",
    "UCyQ1mGd8OC_wSveeL23XFrA": "楓のせどり塾チャンネル",
}
# 新品（家電・日用品・Amazon）系
NEW = {
    "UCqQhJNigSITUfvr5iZCxjsQ": "【家電せどり】こうじ 店舗せどり物販",
    "UCfEgmoYi4lgXSigbFzhlFOQ": "【日用品せどりの王】ヘルビ王",
    "UCuflwKDRtdx3Ct8QBzXRWjA": "せどり名人チャンネル",
    "UCy-03jSxnLigQBbgq22QfrQ": "朝野拓也 [物販総合研究所]",
    "UCWR6VlWOt4WkRGc4YTko02Q": "船原徹雄 [物販総合研究所]",
    "UCF5x9BneVlgdLWrHuEOnVoQ": "作間せどり",
    "UCOIhopd5khzveETF9D2So1w": "せどり-夏",
    "UCHkfR6_VLHyFYfeSalDwS9w": "スズキのせどり物販チャンネル",
    "UCbW_-YjxvW37wuSyRPyS9BQ": "せどり物販、NEWWORLD",
    "UCHPQij1lb58o6168bulbPAg": "まえすけ社長",
}

# 店舗仕入れ企画に寄せるためのタイトルフィルタ
SOURCING_HINTS = ["仕入れ", "店舗", "セカスト", "オフハウス", "ハードオフ", "ブックオフ",
                  "トレファク", "リサイクル", "ドンキ", "イオン", "ゲオ", "同行", "密着",
                  "リサーチ", "利益商品", "爆益", "せどり", "仕入"]


def get(endpoint, **params):
    params["key"] = KEY
    for attempt in range(4):
        r = requests.get(f"{API}/{endpoint}", params=params, timeout=40)
        if r.status_code == 200:
            return r.json()
        if r.status_code in (403, 429):
            print(f"  !! {r.status_code}: {r.text[:300]}", file=sys.stderr)
            return None
        time.sleep(2 ** attempt)
    print(f"  !! failed {endpoint} {r.status_code}", file=sys.stderr)
    return None


def iso_to_sec(d):
    """PT1H2M3S -> 3723"""
    import re
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", d or "")
    if not m:
        return 0
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + s


def uploads_playlist(channel_ids):
    out = {}
    ids = list(channel_ids)
    for i in range(0, len(ids), 50):
        j = get("channels", part="contentDetails", id=",".join(ids[i:i + 50]))
        if not j:
            continue
        for it in j.get("items", []):
            out[it["id"]] = it["contentDetails"]["relatedPlaylists"]["uploads"]
    return out


def list_uploads(playlist_id, cap=250):
    vids, token = [], None
    while len(vids) < cap:
        j = get("playlistItems", part="contentDetails", playlistId=playlist_id,
                maxResults=50, pageToken=token)
        if not j:
            break
        vids += [x["contentDetails"]["videoId"] for x in j.get("items", [])]
        token = j.get("nextPageToken")
        if not token:
            break
    return vids


def video_stats(video_ids):
    out = []
    for i in range(0, len(video_ids), 50):
        j = get("videos", part="snippet,statistics,contentDetails",
                id=",".join(video_ids[i:i + 50]))
        if not j:
            continue
        for it in j.get("items", []):
            st, sn = it.get("statistics", {}), it["snippet"]
            out.append({
                "videoId": it["id"],
                "title": sn["title"],
                "channelId": sn["channelId"],
                "channelTitle": sn["channelTitle"],
                "publishedAt": sn["publishedAt"],
                "views": int(st.get("viewCount", 0)),
                "likes": int(st.get("likeCount", 0)),
                "comments": int(st.get("commentCount", 0)),
                "durationSec": iso_to_sec(it["contentDetails"]["duration"]),
            })
    return out


def stage_videos(args):
    pool = {**USED, **NEW}
    print(f"[1/3] uploads playlist for {len(pool)} channels")
    pls = uploads_playlist(pool)
    all_vids = []
    for cid, pl in pls.items():
        v = list_uploads(pl, cap=args.scan_per_channel)
        print(f"  {pool.get(cid, cid)[:34]:<34} {len(v)} videos")
        all_vids += v
    print(f"[2/3] stats for {len(all_vids)} videos")
    stats = video_stats(all_vids)

    # 長尺（>=8分）かつ仕入れ企画らしいものに限定
    def keep(v):
        if v["durationSec"] < 480:
            return False
        if v["comments"] < args.min_comments:
            return False
        return any(h in v["title"] for h in SOURCING_HINTS)

    cand = [v for v in stats if keep(v)]
    for v in cand:
        v["segment"] = "used" if v["channelId"] in USED else "new"

    used = sorted([v for v in cand if v["segment"] == "used"], key=lambda x: -x["views"])
    new = sorted([v for v in cand if v["segment"] == "new"], key=lambda x: -x["views"])

    # 1チャンネルあたり上限をかけて偏りを防ぐ
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

    (OUT / "candidates.json").write_text(
        json.dumps(cand, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "selected_videos.json").write_text(
        json.dumps(pick, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[3/3] candidates={len(cand)}  selected={len(pick)} "
          f"(used={sum(1 for x in pick if x['segment']=='used')}, "
          f"new={sum(1 for x in pick if x['segment']=='new')})")
    for v in pick:
        print(f"  [{v['segment']}] {v['views']:>8,}v {v['comments']:>4}c  "
              f"{v['channelTitle'][:18]:<18} {v['title'][:52]}")


def fetch_threads(video_id, cap_pages=30):
    threads, token, pages = [], None, 0
    while pages < cap_pages:
        j = get("commentThreads", part="snippet,replies", videoId=video_id,
                maxResults=100, order="relevance", textFormat="plainText",
                pageToken=token)
        if not j:
            break
        for it in j.get("items", []):
            top = it["snippet"]["topLevelComment"]["snippet"]
            reps = [{
                "author": r["snippet"]["authorDisplayName"],
                "text": r["snippet"]["textDisplay"],
                "likes": r["snippet"].get("likeCount", 0),
                "publishedAt": r["snippet"]["publishedAt"],
            } for r in it.get("replies", {}).get("comments", [])]
            threads.append({
                "author": top["authorDisplayName"],
                "text": top["textDisplay"],
                "likes": top.get("likeCount", 0),
                "publishedAt": top["publishedAt"],
                "replyCount": it["snippet"].get("totalReplyCount", 0),
                "replies": reps,
            })
        token = j.get("nextPageToken")
        pages += 1
        if not token:
            break
    return threads


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
                print(f"  [{i}/{len(sel)}] skip (done) {v['title'][:44]}")
                continue
            th = fetch_threads(v["videoId"])
            total += len(th)
            f.write(json.dumps({**v, "threads": th}, ensure_ascii=False) + "\n")
            f.flush()
            print(f"  [{i}/{len(sel)}] {len(th):>4} threads  {v['title'][:44]}")
    print(f"saved -> {out_path}  (+{total} threads this run)")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--stage", choices=["videos", "comments"], required=True)
    p.add_argument("--scan-per-channel", type=int, default=200)
    p.add_argument("--per-channel", type=int, default=3)
    p.add_argument("--n-used", type=int, default=20)
    p.add_argument("--n-new", type=int, default=10)
    p.add_argument("--min-comments", type=int, default=5)
    p.add_argument("--overwrite", action="store_true")
    a = p.parse_args()
    if not KEY:
        sys.exit("YOUTUBE_API_KEY が未設定です。export YOUTUBE_API_KEY=... してください。")
    (stage_videos if a.stage == "videos" else stage_comments)(a)
