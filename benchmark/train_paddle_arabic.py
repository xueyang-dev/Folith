"""CPU-sized PP-OCRv5 Arabic CTC fine-tuning pilot using official weights.

The backbone and unused NRTR branch stay frozen. Only the CTC sequence encoder
and classifier are adapted. Validation pages select the checkpoint; test pages
are evaluated only before training and after that selection.
"""
import argparse
import copy
import json
import math
import os
from pathlib import Path
import random
import resource
import sys
import time

ROOT = Path(__file__).resolve().parent


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    portable = json.dumps(value, ensure_ascii=False, indent=2).replace(str(ROOT.parent) + "/", "")
    temporary = path.with_suffix(".tmp")
    temporary.write_text(portable + "\n")
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=ROOT.parent / ".venv/ocr-training-src")
    parser.add_argument("--dataset", type=Path, default=ROOT.parent / ".audit/paddle-finetune/dataset")
    parser.add_argument("--weights", type=Path, default=ROOT.parent / ".audit/paddle-finetune/pretrained/arabic_PP-OCRv5_mobile_rec_pretrained.pdparams")
    parser.add_argument("--output", type=Path, default=ROOT.parent / ".audit/paddle-finetune/run-01")
    parser.add_argument("--evidence", type=Path, default=ROOT / "paddle-finetune/run-01.json")
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--learning-rate", type=float, default=0.00005)
    parser.add_argument("--accumulate", type=int, default=4)
    parser.add_argument("--augmentations", type=int, default=1)
    parser.add_argument("--seed", type=int, default=20261003)
    args = parser.parse_args()
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("CPU_NUM", "2")
    sys.path.insert(0, str(args.source.resolve()))
    import cv2
    import numpy as np
    import paddle
    import yaml
    from ppocr.modeling.architectures import build_model
    from ppocr.postprocess.rec_postprocess import CTCLabelDecode
    from ppocr.losses.rec_ctc_loss import CTCLoss
    sys.path.insert(0, str(ROOT))
    import bench_lib as B

    paddle.set_device("cpu")
    paddle.seed(args.seed)
    random.seed(args.seed)
    np.random.seed(args.seed)
    args.output.mkdir(parents=True, exist_ok=True)
    dictionary = args.source / "ppocr/utils/dict/ppocrv5_arabic_dict.txt"
    decoder = CTCLabelDecode(str(dictionary), use_space_char=True)
    config_path = args.source / "configs/rec/PP-OCRv5/multi_language/arabic_PP-OCRv5_mobile_rec.yaml"
    config = yaml.safe_load(config_path.read_text())
    char_count = len(decoder.character)
    config["Architecture"]["Head"]["out_channels_list"] = {
        "CTCLabelDecode": char_count, "NRTRLabelDecode": char_count + 3}
    architecture = copy.deepcopy(config["Architecture"])
    model = build_model(copy.deepcopy(architecture))
    weights = paddle.load(str(args.weights))
    state = model.state_dict()
    mismatches = [key for key in state if key not in weights or
                  state[key].shape != weights[key].shape]
    if mismatches:
        raise ValueError(f"Official checkpoint/config mismatch: {mismatches[:10]}")
    model.set_state_dict(weights)
    del weights
    trainable = []
    for name, parameter in model.named_parameters():
        parameter.stop_gradient = not name.startswith(("head.ctc_encoder.", "head.ctc_head."))
        if not parameter.stop_gradient:
            trainable.append(parameter)
    model.eval()  # Keep pretrained BN statistics and dropout fixed for few-shot data.
    manifest = json.loads((args.dataset / "manifest.json").read_text())
    if manifest.get("review_required", True):
        raise ValueError("Dataset contact sheets must be reviewed before training")
    rows = manifest["rows"]
    splits = {s: {r["page_index"] for r in rows if r["split"] == s}
              for s in ("train", "validation", "test")}
    if any(splits[a] & splits[b] for a, b in
           (("train", "validation"), ("train", "test"), ("validation", "test"))):
        raise ValueError("Page leakage between training, validation and test")

    def tensor_image(image):
        # Same aspect-aware BGR normalization as the pinned PaddleX recognizer.
        h, w = image.shape[:2]
        width = min(3200, int(max(320, 48 * w / h)))
        resized_width = min(width, math.ceil(48 * w / h))
        image = cv2.resize(image, (resized_width, 48)).astype("float32")
        result = np.zeros((3, 48, width), dtype="float32")
        result[:, :, :resized_width] = (image.transpose(2, 0, 1) / 255 - .5) / .5
        return paddle.to_tensor(result[None])

    def target(label):
        # Official Arabic decoder maps visual CTC order to logical Unicode.
        # Use its inverse for training, preserving the canonical logical label.
        visual = decoder.pred_reverse(label)
        if decoder.pred_reverse(visual) != label:
            raise ValueError(f"Arabic target does not round-trip: {label}")
        missing = set(visual) - set(decoder.dict)
        if missing:
            raise ValueError(f"Unsupported label characters: {missing}")
        return paddle.to_tensor([[decoder.dict[c] for c in visual]], dtype="int32")

    started = time.perf_counter()
    evidence = {"status": "preparing", "paddle_version": paddle.__version__,
                "upstream_tag": "v3.7.0", "upstream_commit": "b03f46425e8ff4442b268ce449e3eef758146cd4",
                "device": "cpu", "model": config["Global"]["model_name"],
                "trainable_parameters": sum(int(p.numel()) for p in trainable),
                "frozen": "backbone, NRTR, pretrained batch-normalization statistics",
                "dataset": str(args.dataset / "manifest.json"),
                "page_splits": manifest["page_splits"],
                "epochs": args.epochs, "learning_rate": args.learning_rate,
                "gradient_accumulation": args.accumulate, "augmentations_per_train_line": args.augmentations,
                "checkpoint_selection": "minimum validation character error rate, including unchanged baseline",
                "history": []}
    write_json(args.evidence, evidence)
    samples = {s: [] for s in splits}
    for row in rows:
        if row["split"] == "train" and not row["training_eligible"]:
            continue
        image = cv2.imread(str(args.dataset / row["image"]))
        if image is None:
            raise ValueError(f"Unreadable row image: {row['image']}")
        variants = [image]
        if row["split"] == "train":
            for n in range(args.augmentations):
                adjusted = np.clip(image.astype("float32") * .95 + 4, 0, 255).astype("uint8")
                variants.append(cv2.GaussianBlur(adjusted, (3, 3), .45))
        label = target(row["text"])
        for n, variant in enumerate(variants):
            with paddle.no_grad():
                feature = model.backbone(tensor_image(variant)).detach()
            samples[row["split"]].append({"row": row, "feature": feature, "target": label,
                                          "augmented": n > 0})
        if len(samples[row["split"]]) % 10 == 0:
            print("features", row["split"], len(samples[row["split"]]), flush=True)
    evidence["feature_preparation_s"] = time.perf_counter() - started
    evidence["line_counts"] = {s: sum(not r["augmented"] for r in group)
                               for s, group in samples.items()}

    def evaluate(group):
        model.eval()
        outputs = []
        with paddle.no_grad():
            for sample in group:
                probabilities = model.head.ctc_head(model.head.ctc_encoder(sample["feature"]))
                text, confidence = decoder(probabilities.numpy())[0]
                ref = sample["row"]["text"]
                outputs.append({"page_index": sample["row"]["page_index"],
                    "line_index": sample["row"]["line_index"], "reference": ref, "text": text,
                    "confidence": confidence, "character_errors": B.cer(ref, text),
                    "word_errors": B.wer(ref, text)})
        chars = sum(o["character_errors"]["ref_len"] for o in outputs)
        words = sum(o["word_errors"]["ref_words"] for o in outputs)
        return {"cer": sum(sum(o["character_errors"][k] for k in ("sub", "del", "ins"))
                           for o in outputs) / chars,
                "wer": sum(sum(o["word_errors"][k] for k in ("sub", "del", "ins"))
                           for o in outputs) / words,
                "character_errors": {k: sum(o["character_errors"][k] for o in outputs)
                                     for k in ("sub", "del", "ins", "ref_len")},
                "lines": outputs}

    evidence["baseline_validation"] = evaluate(samples["validation"])
    evidence["baseline_test"] = evaluate(samples["test"])
    best_cer = evidence["baseline_validation"]["cer"]
    best_epoch = 0
    paddle.save(model.state_dict(), str(args.output / "best.pdparams"))
    optimizer = paddle.optimizer.Adam(learning_rate=args.learning_rate,
        parameters=trainable, grad_clip=paddle.nn.ClipGradByGlobalNorm(5.0), weight_decay=0.00003)
    loss_function = CTCLoss()
    evidence["status"] = "training"
    write_json(args.evidence, evidence)
    print("baseline validation CER", best_cer, "test CER", evidence["baseline_test"]["cer"], flush=True)
    training = samples["train"]
    for epoch in range(1, args.epochs + 1):
        epoch_started = time.perf_counter()
        random.shuffle(training)
        losses = []
        model.eval()
        model.head.ctc_head.train()  # logits for the official CTC loss, no NRTR forward.
        optimizer.clear_grad()
        for start in range(0, len(training), args.accumulate):
            batch = training[start:start + args.accumulate]
            for sample in batch:
                predictions = model.head.ctc_head(model.head.ctc_encoder(sample["feature"]))
                length = paddle.to_tensor([sample["target"].shape[1]], dtype="int64")
                loss = loss_function(predictions, [None, sample["target"], length])["loss"]
                if not bool(paddle.isfinite(loss).item()):
                    raise ValueError("Nonfinite CTC loss")
                (loss / len(batch)).backward()
                losses.append(float(loss.item()))
            optimizer.step()
            optimizer.clear_grad()
        validation = evaluate(samples["validation"])
        row = {"epoch": epoch, "mean_training_ctc_loss": sum(losses) / len(losses),
               "validation_cer": validation["cer"], "validation_wer": validation["wer"],
               "seconds": time.perf_counter() - epoch_started}
        evidence["history"].append(row)
        if validation["cer"] < best_cer:
            best_cer, best_epoch = validation["cer"], epoch
            paddle.save(model.state_dict(), str(args.output / "best.pdparams"))
            paddle.save(optimizer.state_dict(), str(args.output / "best.pdopt"))
            write_json(args.output / "best-validation.json", validation)
        evidence["best_epoch"] = best_epoch
        evidence["best_validation_cer"] = best_cer
        evidence["elapsed_s"] = time.perf_counter() - started
        write_json(args.evidence, evidence)
        print(json.dumps(row), "best", best_epoch, flush=True)
    model.set_state_dict(paddle.load(str(args.output / "best.pdparams")))
    evidence["selected_validation"] = evaluate(samples["validation"])
    evidence["selected_test"] = evaluate(samples["test"])
    evidence["selected_train"] = evaluate([r for r in training if not r["augmented"]])
    evidence["elapsed_s"] = time.perf_counter() - started
    evidence["peak_rss_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    evidence["status"] = "completed"
    evidence["checkpoint"] = str(args.output / "best.pdparams")
    write_json(args.evidence, evidence)
    # Export config retains the original full architecture and character dictionary.
    config["Architecture"] = architecture
    config["Global"].update(use_gpu=False, distributed=False, pretrained_model=str(args.output / "best.pdparams"),
        character_dict_path=str(dictionary.resolve()), save_inference_dir=str(args.output / "inference"))
    (args.output / "export.yaml").write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True))
    print("FINAL", evidence["best_epoch"], evidence["baseline_test"]["cer"],
          evidence["selected_test"]["cer"], "seconds", evidence["elapsed_s"], flush=True)


if __name__ == "__main__":
    main()
