# A02 の動画の生成依頼（generate_video_batch の requests）を計画・media の対応から作る作業用スクリプト。media.json・jobs.json は tmp/hf（Git の管理外）
# 計画の videos と media.json・jobs.json から generate_video_batch の requests を作る。引数: カットID（カンマ区切り）
import json, sys, yaml
plan = yaml.safe_load(open("episodes/A/scenes/A02/gen/A02_plan.yaml"))
m = json.load(open("tmp/hf/media.json")); j = json.load(open("tmp/hf/jobs.json"))
VIEW = {"bisco": "characters/bisco/views/bisco_front.png", "hundred-knight": "characters/hundred-knight/views/hundred-knight_front.png", "arlecchino": "characters/arlecchino/views/arlecchino_front.png"}
want = sys.argv[1].split(","); reqs = []; meta = []
for v in plan["videos"]["items"]:
    if v["cut"] not in want: continue
    med = [{"role": "start_image", "value": j[v["start_image"]]}]
    refs = [v["start_image"]]
    if v["end_image"]:
        med.append({"role": "end_image", "value": j[v["end_image"]]}); refs.append(v["end_image"])
    for r in v["image_references"]:
        who = r.split("/")[1].replace("_sheet_01.png", "")
        med.append({"role": "image_references", "value": m[VIEW[who]]}); refs.append(VIEW[who])
    med.append({"role": "video_references", "value": m[v["previs"]]}); refs.append(v["previs"])
    if v["audio_reference"]:
        a = v["audio_reference"].split("（")[0]
        med.append({"role": "audio_references", "value": m[a]}); refs.append(a)
    i = int(v["cut"][-3:])
    v["prompt"] = v["prompt"].replace(" 2D TV anime style", " Video 1 is a rough blocking animation with grey placeholder figures: follow its timing, positions, movement and camera, but do not draw the placeholders. 2D TV anime style", 1)
    reqs.append({"index": i, "params": {"model": "wan3_0", "prompt": v["prompt"], "duration": v["duration"], "resolution": "480p", "aspect_ratio": "16:9", "generate_audio": False, "medias": med}})
    meta.append({"index": i, "cut": v["cut"], "duration": v["duration"], "refs": refs, "prompt": v["prompt"], "output": f"episodes/A/scenes/A02/video/{v['cut']}_wan3_0_480p_01.mp4"})
json.dump(meta, open("tmp/hf/vbatch.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps(reqs, ensure_ascii=False))
