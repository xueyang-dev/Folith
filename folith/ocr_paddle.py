"""Optional, version-pinned local PP-OCRv5 adapter. No import-time inference."""
from __future__ import annotations

import importlib.metadata
import io
import hashlib
import logging
import multiprocessing
import os
import platform
import sys
import time
from pathlib import Path

from folith import ocr_models
from folith.ocr_evidence import OCRBackend, OCRBlock, OCRRuntimeInfo
from folith.ocr_language import resolve_ocr_language
from folith.ocr_order import order_lines

PADDLE_VERSIONS = {"paddleocr": "3.7.0", "paddlex": "3.7.2", "paddlepaddle": "3.3.1"}
# Restricted to Folith's published language registry; unsupported choices fail.
LANGUAGES = {"eng": "en", "ara": "ar", "fas": "fa", "urd": "ur",
             "chi_sim": "ch", "chi_tra": "chinese_cht", "jpn": "japan",
             "kor": "korean", "rus": "ru", "deu": "de", "fra": "fr",
             "spa": "es", "por": "pt", "ita": "it", "nld": "nl",
             "tur": "tr", "vie": "vi", "hin": "hi"}


class OCRUnavailable(RuntimeError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def model_root(model_id=None):
    """Resource root of the official model set (kept for compatibility).

    Task-level selection goes through :class:`PaddleOCRBackend`; this helper
    only answers "where does the stock model live", which is the documented
    meaning of ``FOLITH_OCR_MODEL_DIR``.
    """
    option = ocr_models.try_resolve(model_id or ocr_models.OFFICIAL_MODEL_ID)
    if option is None:
        option = ocr_models.resolve(ocr_models.OFFICIAL_MODEL_ID)
    return ocr_models.resource_root(option)


class PaddleOCRBackend(OCRBackend):
    name = "paddleocr"
    display_name = "PaddleOCR · PP-OCRv5"
    max_workers = 1

    def __init__(self, language="eng", *, _in_process=False, model_id=None,
                 resource_dir=None):
        import dataclasses
        self.language = language or "eng"
        self.model_id = str(model_id or "").strip() or ocr_models.OFFICIAL_MODEL_ID
        option = ocr_models.try_resolve(self.model_id)
        if resource_dir:
            # The worker receives the exact resource location the parent chose,
            # so a model selected in one task is the model this worker loads.
            base = {"model_id": self.model_id, "label": self.model_id,
                    "resource_dir": str(resource_dir)}
            option = (dataclasses.replace(option, resource_dir=str(resource_dir))
                      if option is not None
                      else ocr_models.PaddleModelOption(**base))
        self._model_option = option
        self._engine = None
        self._state = "available"
        self.load_seconds = 0.0
        self.device = "cpu"
        self._import_error = ""
        self._asset_version = None
        self._in_process = _in_process
        self._process = None
        self._connection = None
        self._last_timings = {}
        self._last_request = {}
        self._cancel_check = None

    # -- task-level model selection --------------------------------------
    def _option(self):
        if self._model_option is None:
            raise OCRUnavailable(
                "model_unavailable",
                f"找不到已注册的 Paddle 模型「{self.model_id}」；请重新选择模型，"
                "不会自动改用其他模型。")
        return self._model_option

    def model_resource_root(self):
        return ocr_models.resource_root(self._option())

    def model_resource_dir(self):
        return ocr_models.resource_base(self._option())

    def _model_missing_message(self):
        option = self._option()
        return (f"未找到模型资源：{ocr_models.resource_root(option)}。"
                "请安装/复制该模型，或改选其他 Paddle 模型；不会自动下载或改用它模型。")

    def _language(self):
        option = self._option()
        resolved = resolve_ocr_language(self.language)
        codes = resolved.engine_code.split("+")
        if len(codes) > 1 and codes[1:] != ["eng"]:
            raise OCRUnavailable("unsupported", "PaddleOCR 暂不支持该语言组合；请选择 Tesseract。")
        if codes[0] not in LANGUAGES:
            raise OCRUnavailable("unsupported", "PP-OCRv5 不支持所选语言；请选择 Tesseract。")
        if not ocr_models.language_supported(option, codes[0]):
            raise OCRUnavailable(
                "language_out_of_scope",
                f"模型「{option.label}」只在{ocr_models.language_scope_label(option)}上验证过，"
                "不能用于当前 OCR 语言；请改选官方模型或 Tesseract。")
        return LANGUAGES[codes[0]]

    def model_names(self):
        language = self._language()
        if language in {"ar", "fa", "ur"}:
            rec = "arabic_PP-OCRv5_mobile_rec"
        elif language in {"ch", "chinese_cht", "japan"}:
            rec = "PP-OCRv5_server_rec"
        elif language == "en":
            rec = "en_PP-OCRv5_mobile_rec"
        elif language == "ru":
            rec = "eslav_PP-OCRv5_mobile_rec"
        elif language == "hi":
            rec = "devanagari_PP-OCRv5_mobile_rec"
        elif language == "korean":
            rec = "korean_PP-OCRv5_mobile_rec"
        else:
            rec = "latin_PP-OCRv5_mobile_rec"
        return "PP-OCRv5_server_det", rec

    def capabilities(self):
        return {"languages": list(LANGUAGES), "scripts": ["Arabic", "Latin", "Han", "Kana", "Hangul", "Cyrillic", "Devanagari"],
                "cpu": True, "gpu": False, "rtl": True, "line_boxes": True,
                "word_boxes": False, "confidence_kind": "paddle_rec_score_0_1", "max_workers": 1,
                "platforms": ["macOS arm64", "Windows x86_64", "Linux x86_64"],
                "python": "3.10–3.13"}

    def quality_note(self):
        return ("Paddle 阿拉伯语模型在实测中会遗漏西文数字、误识英文姓名；混排文字请核对原页。"
                if self._language() in {"ar", "fa", "ur"} else "")

    def status(self):
        try:
            option = self._option()
        except OCRUnavailable as exc:
            return {"state": "model_unavailable", "message": str(exc),
                    "model_id": self.model_id}
        try:
            self._language()
            machine = platform.machine().lower()
            if (sys.platform not in {"darwin", "win32", "linux"} or
                    (sys.platform == "darwin" and machine != "arm64") or
                    (sys.platform != "darwin" and machine not in {"x86_64", "amd64"}) or
                    not (3, 10) <= sys.version_info[:2] <= (3, 13)):
                return {"state": "unsupported", "message": "该平台没有受支持的 Paddle CPU runtime。",
                        "model_id": self.model_id}
            for package, version in PADDLE_VERSIONS.items():
                if package in sys.modules and sys.modules[package] is None:
                    raise importlib.metadata.PackageNotFoundError(package)
                actual = importlib.metadata.version(package)
                if actual != version:
                    return {"state": "unsupported", "message": "PaddleOCR runtime 版本不匹配；请重新安装兼容的可选 OCR 组件。",
                            "model_id": self.model_id}
        except importlib.metadata.PackageNotFoundError:
            return {"state": "runtime_missing", "message": "PaddleOCR 未安装；请安装可选 OCR 组件，或选择 Tesseract。",
                    "model_id": self.model_id}
        except (ValueError, OCRUnavailable) as exc:
            return {"state": "unsupported", "message": str(exc),
                    "model_id": self.model_id}
        if self._state in {"initializing", "failed", "ready"}:
            return {"state": self._state, "message": self._import_error,
                    "model_id": self.model_id}
        # The resource directory must exist for a book-specific model: it is not
        # downloadable, so "missing" is a hard state rather than "download later".
        if option.resource_dir and not ocr_models.resource_base(option).is_dir():
            return {"state": "model_missing", "message": self._model_missing_message(),
                    "model_id": self.model_id, "model_resource_dir": str(self.model_resource_dir())}
        ready = all(self._model_ready(self.model_resource_root() / "official_models" / name)
                    for name in self.model_names())
        if ready:
            return {"state": "available", "message": "模型已安装；将在 OCR 时加载。",
                    "model_id": self.model_id, "model_resource_dir": str(self.model_resource_dir())}
        if not option.allow_download:
            return {"state": "model_missing", "message": self._model_missing_message(),
                    "model_id": self.model_id, "model_resource_dir": str(self.model_resource_dir())}
        return {"state": "model_missing", "message": "首次使用 PaddleOCR 时下载模型。",
                "model_id": self.model_id, "model_resource_dir": str(self.model_resource_dir())}

    @staticmethod
    def _model_ready(path):
        return (path / "inference.yml").is_file() and (path / "inference.pdiparams").is_file() and (
            (path / "inference.json").is_file() or (path / "inference.pdmodel").is_file())

    def available(self):
        state = self.status()["state"]
        if state in {"available", "initializing", "ready"}:
            return True
        # "model_missing" only means usable when the assets can be fetched.  A
        # local book model with absent resources must not be advertised.
        if state == "model_missing":
            option = self._model_option
            return bool(option is not None and option.allow_download)
        return False

    def unavailable_reason(self):
        return "" if self.available() else self.status()["message"]

    def _model_version(self):
        if self._asset_version is None:
            directories = [self.model_resource_root() / "official_models" / name
                           for name in self.model_names()]
            if all(self._model_ready(path) for path in directories):
                parts = []
                for directory in directories:
                    digest = hashlib.sha256()
                    for name in ("inference.pdiparams", "inference.yml", "inference.json", "inference.pdmodel"):
                        path = directory / name
                        if path.is_file():
                            digest.update(name.encode())
                            digest.update(path.read_bytes())
                    parts.append(directory.name + "=" + digest.hexdigest())
                card = ocr_models.card_digest(self._option())
                if card:
                    parts.append("model_card=" + card)
                self._asset_version = ";".join(parts)
        return self._asset_version or "models_not_installed"

    def runtime_info(self, language, *, include_model_version=True):
        option = self._model_option
        try:
            models = self.model_names()
        except (ValueError, OCRUnavailable):
            models = ("PP-OCRv5", "unsupported")
        model_name = "+".join(models)
        if option is not None and option.model_id != ocr_models.OFFICIAL_MODEL_ID:
            # The model set is part of the reported model identity, so a report
            # can never claim the official model produced these blocks.
            model_name += "@" + option.model_id
        version = ";".join(f"{k}={v}" for k, v in PADDLE_VERSIONS.items())
        return OCRRuntimeInfo(self.name, model_name, self.device, "paddleocr",
            "cuda" if self.device.startswith("gpu") else "native_cpu",
            backend_version=version, extra={"language": language,
                "engine_language": self._language() if models[1] != "unsupported" else "",
                "model_version": (self._model_version() if models[1] != "unsupported" else "unsupported") if include_model_version else "not_inspected",
                "model_id": self.model_id,
                "model_label": option.label if option is not None else self.model_id,
                "model_resource_dir": str(self.model_resource_dir()) if option is not None else "",
                "model_language_scope": (ocr_models.language_scope_label(option) if option is not None else ""),
                "model_download_allowed": bool(option.allow_download) if option is not None else False,
                "inference_config": "PP-OCRv5;doc_orientation=false;unwarping=false;line_orientation=false;rec_score=0;det_limit=960;det_limit_type=max;device=" + os.environ.get("FOLIOTHREAD_OCR_DEVICE", "cpu"),
                "confidence_kind": "paddle_rec_score_0_1", "model_load_s": self.load_seconds,
                "model_directory": str(self.model_resource_root()) if option is not None else "",
                "state": self._state})

    def load(self, on_status=None, cancel_check=None):
        if cancel_check is not None:
            self._cancel_check = cancel_check
        if self._engine is not None:
            return
        if self._in_process:
            return self._load_local(on_status)
        if self._process is not None and self._process.is_alive():
            return
        status = self.status()
        if not self.available():
            raise OCRUnavailable(status["state"], status["message"])
        self.close()
        self._state = "initializing"
        started = time.perf_counter()
        option = self._option()
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        self._connection = parent
        # The model selection crosses the process boundary as explicit worker
        # arguments.  It is never applied by mutating the parent's environment,
        # so two tasks with different models can run in the same process.
        self._process = context.Process(target=_paddle_worker,
            args=(child, self.language, str(self.model_resource_dir()),
                  bool(option.allow_download), option.model_id),
            name="folith-paddle-ocr", daemon=True)
        try:
            self._process.start()
            child.close()
            response = self._receive(600, on_status)
            if response["kind"] == "error":
                raise OCRUnavailable(response["code"], response["message"])
            self.device = response["device"]
            self._state = "ready"
        except (OCRUnavailable, OSError) as exc:
            self.close()
            self._state = "failed"
            self._import_error = str(exc)
            if isinstance(exc, OCRUnavailable):
                raise
            raise OCRUnavailable("runtime_unavailable", "Paddle OCR 进程无法启动；检查可选 runtime 安装。") from exc
        except RuntimeError:
            self.close()
            self._state = "available"
            raise
        finally:
            child.close()
            self.load_seconds += time.perf_counter() - started

    def _receive(self, timeout, on_status=None):
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                if self._cancel_check and self._cancel_check():
                    raise RuntimeError("任务已请求取消")
                if self._connection.poll(.1):
                    message = self._connection.recv()
                    if message["kind"] == "status":
                        if on_status:
                            on_status(message["message"])
                        continue
                    return message
                if not self._process.is_alive():
                    raise EOFError
        except (EOFError, OSError):
            if self._process is not None:
                self._process.join(timeout=.1)
            code = self._process.exitcode if self._process else None
            logging.getLogger(__name__).error("Paddle worker terminated; exit code=%s", code)
            raise OCRUnavailable("worker_terminated", "Paddle OCR 进程异常退出；已完成页面保留，可减少资源占用后重试。")
        raise OCRUnavailable("inference_timeout", "Paddle OCR 处理超时；已完成页面保留。")

    def _load_local(self, on_status=None):
        """Runs only inside the single isolated inference worker."""
        status = self.status()
        if not self.available():
            raise OCRUnavailable(status["state"], status["message"])
        option = self._option()
        self._state = "initializing"
        if on_status:
            on_status("Initializing PaddleOCR · PP-OCRv5" +
                      (" · " + option.label if option.resource_dir else "") +
                      (" · Downloading models" if status["state"] == "model_missing" else ""))
        started = time.perf_counter()
        stage = "runtime_unavailable"
        try:
            root = self.model_resource_root()
            os.environ["PADDLE_PDX_CACHE_HOME"] = str(root)
            os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
            downloads = root / "downloads" / "huggingface"
            os.environ["HF_HOME"] = str(downloads)
            os.environ["HF_HUB_CACHE"] = str(downloads / "hub")
            os.environ["HF_XET_CACHE"] = str(downloads / "xet")
            from paddleocr import PaddleOCR
            import paddle
            from paddlex.utils.cache import CACHE_DIR
            if Path(CACHE_DIR).resolve() != root:
                raise OCRUnavailable("model_unavailable", "Paddle 已从其他模型目录加载；请重启 Folith 后重试。")
            requested = os.environ.get("FOLIOTHREAD_OCR_DEVICE", "cpu").lower()
            self.device = "cpu" if requested in {"", "auto", "cpu"} else requested.replace("cuda", "gpu")
            if self.device != "cpu":
                raise OCRUnavailable("unsupported_device", "当前可选组件支持 CPU；请选择 CPU runtime。")
            names = self.model_names()
            # Verify the registry against the installed, exactly pinned 3.x API.
            probe = object.__new__(PaddleOCR)
            if probe._get_ocr_model_names(self._language(), "PP-OCRv5") != names:
                raise OCRUnavailable("unsupported", "PP-OCRv5 模型配置与已安装 runtime 不一致。")
            from paddlex.inference.utils.official_models import official_models
            dirs = []
            for name in names:
                path = root / "official_models" / name
                if self._model_ready(path):
                    dirs.append(str(path))
                    continue
                if not option.allow_download:
                    # Never substitute the official weights for a book model.
                    stage = "model_missing"
                    raise OCRUnavailable(stage, self._model_missing_message())
                stage = "model_download_failed"
                dirs.append(str(official_models[name]))
            stage = "model_load_failed"
            self._engine = PaddleOCR(
                text_detection_model_name=names[0], text_detection_model_dir=dirs[0],
                text_recognition_model_name=names[1], text_recognition_model_dir=dirs[1],
                use_doc_orientation_classify=False, use_doc_unwarping=False,
                use_textline_orientation=False, device=self.device,
                enable_mkldnn=False, cpu_threads=2, text_rec_score_thresh=0.0,
                text_det_limit_side_len=960, text_det_limit_type="max")
            self._state = "ready"
        except OCRUnavailable as exc:
            self._state = "failed"
            self._import_error = str(exc)
            raise
        except Exception as exc:
            logging.getLogger(__name__).exception("Paddle model initialization failed")
            self._state = "failed"
            self._import_error = "Paddle 模型加载失败；检查网络、模型目录和可选 runtime 安装。"
            raise OCRUnavailable(stage, self._import_error) from exc
        finally:
            self.load_seconds = time.perf_counter() - started

    def ocr_page(self, png_bytes, *, page_index, page_number, language, dpi):
        self._last_timings = {}
        try:
            self.load()
        except OCRUnavailable as exc:
            return [], f"{exc.code}: {exc}"
        if not self._in_process:
            try:
                self._connection.send((png_bytes, dict(page_index=page_index,
                    page_number=page_number, language=language, dpi=dpi)))
                response = self._receive(180)
                self._last_timings = response.get("timings", {})
                return [OCRBlock(**block) for block in response["blocks"]], response["error"]
            except (OCRUnavailable, OSError) as exc:
                self.close()
                code = getattr(exc, "code", "worker_terminated")
                if code in {"worker_terminated", "inference_timeout"}:
                    self._state = "available"
                return [], f"{code}: 第 {page_number} 页识别失败；已完成页面保留。"
        stage = "invalid_image"
        try:
            self.load()
            import numpy as np
            from PIL import Image
            image = np.asarray(Image.open(io.BytesIO(png_bytes)).convert("RGB"))[:, :, ::-1].copy()
            stage = "inference_error"
            started = time.perf_counter()
            results = list(self._engine.predict(image))
            self._last_timings["inference_s"] = time.perf_counter() - started
            stage = "result_parsing_error"
            started = time.perf_counter()
            lines = []
            for result in results:
                texts, scores, polygons = result["rec_texts"], result["rec_scores"], result["rec_polys"]
                if not len(texts) == len(scores) == len(polygons):
                    raise ValueError("Paddle recognition arrays have inconsistent lengths")
                for text, score, polygon in zip(texts, scores, polygons):
                    if not np.isfinite(float(score)) or not 0 <= float(score) <= 1:
                        raise ValueError("Paddle returned invalid recognition confidence")
                    points = np.asarray(polygon, dtype=float)
                    if points.shape != (4, 2) or not np.isfinite(points).all():
                        raise ValueError("Paddle returned invalid line geometry")
                    if not text.strip():
                        continue
                    lines.append({"text": str(text), "left": float(points[:, 0].min()),
                        "top": float(points[:, 1].min()), "right": float(points[:, 0].max()),
                        "bottom": float(points[:, 1].max()), "engine_confidence": float(score),
                        "confidence_kind": "paddle_rec_score_0_1", "polygon": points.tolist()})
            direction = resolve_ocr_language(language).direction
            lines = order_lines(lines, direction)
            self._last_timings["reading_order_s"] = time.perf_counter() - started
            info = self.runtime_info(language)
            return [OCRBlock(text=line["text"], left=line["left"], top=line["top"],
                right=line["right"], bottom=line["bottom"], confidence=None,
                engine_confidence=line["engine_confidence"], confidence_kind="paddle_rec_score_0_1",
                page_index=page_index, page_number=page_number, backend=self.name,
                device=info.device, provider=info.provider, model=info.model,
                backend_version=info.backend_version, raw_text=line["text"], lines=[line]) for line in lines], None
        except OCRUnavailable as exc:
            return [], f"{exc.code}: {exc}"
        except Exception as exc:
            logging.getLogger(__name__).exception("Paddle page %s failed", page_number)
            code = "out_of_memory" if isinstance(exc, MemoryError) or "out of memory" in str(exc).lower() else stage
            return [], f"{code}: 第 {page_number} 页识别失败；技术日志保留了详情。"

    def close(self):
        self._engine = None
        if self._state == "ready":
            self._state = "available"
        if self._process is not None:
            if self._process.pid is not None:
                if self._process.is_alive():
                    self._process.terminate()
                self._process.join(timeout=5)
                if self._process.is_alive():
                    self._process.kill()
                    self._process.join(timeout=5)
            self._process.close()
            self._process = None
        if self._connection is not None:
            self._connection.close()
            self._connection = None


def _paddle_worker(connection, language, resource_dir="", allow_download=True,
                   model_id=ocr_models.OFFICIAL_MODEL_ID):
    """Only canonical primitives cross the pipe; native failures kill this worker."""
    backend = PaddleOCRBackend(language, _in_process=True, model_id=model_id,
                               resource_dir=resource_dir or None)
    try:
        backend.load(lambda message: connection.send({"kind": "status", "message": message}))
        connection.send({"kind": "ready", "device": backend.device})
        while True:
            png_bytes, kwargs = connection.recv()
            blocks, error = backend.ocr_page(png_bytes, **kwargs)
            from dataclasses import asdict
            connection.send({"kind": "page", "blocks": [asdict(block) for block in blocks],
                             "error": error, "timings": backend._last_timings})
    except OCRUnavailable as exc:
        connection.send({"kind": "error", "code": exc.code, "message": str(exc)})
    except (EOFError, BrokenPipeError):
        pass
    finally:
        backend.close()
        connection.close()
