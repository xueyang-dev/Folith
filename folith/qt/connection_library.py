"""Saved local connections and role selection for the settings window."""
from __future__ import annotations

import uuid
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QVBoxLayout, QWidget)
from . import adapter, components, connection_status, i18n, icons, style
from . import theme as theme_module
from folith.model_connections import effective_config


class ConnectionLibrary(QWidget):
    """设置 · 模型（阶段 1 画布）：「连接」列表 + 「用途」分组 + 一个「测试并保存」。

    添加 / 编辑连接在一张单独的面板（:attr:`editor`）里，点「添加连接」或「编辑」才出现；
    页面本身保持很短。编辑面板里的控件仍是设置窗口原来那几个（提供商、模型、密钥、
    地址），测试连接和获取模型走的是与真正翻译相同的路径。
    """

    STATE_TEXT = {"ok": "已连接", "failed": "连接失败", "untested": "未测试",
                  "missing": "未配置完整"}
    STATE_TONE = {"ok": "done", "failed": "error", "untested": "attention",
                  "missing": "attention"}
    ROLES = (("translation", "翻译"), ("reviewer", "审校"), ("vision", "视觉"))

    def __init__(self, dialog):
        super().__init__()
        from .dialogs import _setting_row
        self.dialog = dialog
        self.profiles = [dict(p) for p in dialog.config.connections]
        self.roles = dict(dialog.config.connection_roles)
        self.role_models = dict(dialog.config.connection_models)
        self.model_selectors = {}
        self.editing = ""
        #: 编辑区里尚未保存的那条连接的 id：多次读取编辑区时保持不变。
        self._pending_id = ""
        #: 编辑区当前这条连接已保存的思考开关；模型不支持该控件时按它原样写回。
        self._thinking_mode = ""
        #: 模型目录声明过的输入模态（#48）：来自「获取可用模型」或正在编辑的
        #: 已保存连接。换提供商即作废——声明是跟着目录走的。
        self._catalog_modalities = {}
        #: 「测试视觉」进行中：按钮去抖。
        self._vision_probe_busy = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme_module.SPACE["md"])

        # ---- 连接列表 -------------------------------------------------------
        self.add_button = QPushButton(i18n.tr('添加连接'))
        style.set_button(self.add_button, "plain", small=True)
        self.add_button.setIcon(icons.icon("plus", 14, "accent"))
        self.add_button.setIconSize(QSize(14, 14))
        self.add_button.clicked.connect(self.open_editor)
        self.list_group = components.FormGroup()
        layout.addWidget(self.list_group)

        # ---- 用途：翻译 / 审校 / 视觉，各选一个已保存的连接和它的模型 ----------------
        dialog._vision_view = adapter.vision_connection_view()
        dialog.vision_connection_box = style.ComboBox()
        self.selectors = {}
        self.capability_warnings: dict[str, "components.StatusLabel"] = {}
        self.role_group = components.FormGroup()
        self.role_group.add_row(self._group_header(i18n.tr('用途')))
        for role, label in self.ROLES:
            box = dialog.vision_connection_box if role == "vision" else style.ComboBox()
            box.setSizeAdjustPolicy(box.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            box.setMinimumContentsLength(12)
            box.setAccessibleName(i18n.tr(connection_status.ROLE_LABELS[role]))
            box.currentIndexChanged.connect(lambda _i, r=role: self.select(r))
            model_box = self.make_model_selector(role)
            if role != "vision":
                self.selectors[role] = box
            self.role_group.add_row(_setting_row(i18n.tr(label), box, model_box))
            if role == "vision":
                # #48：所选视觉模型若是「已知仅文本」给出警告；未知如实说未知。
                hint = components.StatusLabel("", "idle")
                hint.dot.hide()
                hint.setVisible(False)
                holder = QWidget()
                holder_row = QHBoxLayout(holder)
                holder_row.setContentsMargins(theme_module.SPACE["lg"], 0,
                                               theme_module.SPACE["md"], 0)
                holder_row.addWidget(hint)
                self.role_group.add_row(holder)
                self.vision_modality_hint = hint
            # #258：模型能力警告（仅 OCR / 仅翻译，不适合当前用途）。
            cap_warn = components.StatusLabel("", "error")
            cap_warn.dot.hide()
            cap_warn.setVisible(False)
            cap_holder = QWidget()
            cap_row = QHBoxLayout(cap_holder)
            cap_row.setContentsMargins(theme_module.SPACE["lg"], 0,
                                       theme_module.SPACE["md"], 0)
            cap_row.addWidget(cap_warn)
            self.role_group.add_row(cap_holder)
            self.capability_warnings[role] = cap_warn
        layout.addWidget(self.role_group)
        layout.addStretch(1)
        layout.addWidget(dialog._apply_row("models"))

        self._build_editor()
        self.refresh()
        self.reset()
        self._prefill_from_config()

    @staticmethod
    def _group_header(title, *controls):
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(theme_module.SPACE["md"], theme_module.SPACE["sm"],
                               theme_module.SPACE["sm"], theme_module.SPACE["xs"])
        label = QLabel(title)
        style.set_text_role(label, "section")
        row.addWidget(label, 1)
        for control in controls:
            row.addWidget(control)
        return holder

    # ---- 添加 / 编辑连接的面板 ------------------------------------------------------
    def _build_editor(self):
        from .dialogs import _setting_row
        dialog = self.dialog
        self.editor = QDialog(dialog)
        self.editor.setObjectName("connectionEditor")
        self.editor.setWindowTitle(i18n.tr('添加连接'))
        self.editor.setModal(True)
        self.editor.setMinimumWidth(520)
        body = QVBoxLayout(self.editor)
        body.setContentsMargins(theme_module.SPACE["xl"], theme_module.SPACE["lg"],
                                theme_module.SPACE["xl"], theme_module.SPACE["lg"])
        body.setSpacing(theme_module.SPACE["md"])
        self.editor_title = QLabel(i18n.tr('添加连接'))
        style.set_text_role(self.editor_title, "card-title")
        body.addWidget(self.editor_title)
        self.kind = style.SegmentedControl([
            ("catalog", i18n.tr('模型提供商')), ("custom", i18n.tr('自定义 API'))])
        self.kind.set_value("catalog")
        self.kind.changed.connect(self.change_kind)
        body.addWidget(self.kind)

        fields = components.FormGroup()
        self.provider_row = _setting_row(i18n.tr('提供商'), dialog.provider_box)
        dialog.provider_box.setMinimumWidth(240)
        self.provider_id = QLineEdit()
        self.provider_id.setMinimumWidth(240)
        self.id_row = _setting_row(i18n.tr('提供商 ID'), self.provider_id)
        self.name = QLineEdit()
        self.name.setMinimumWidth(240)
        self.name.setPlaceholderText(i18n.tr('留空则用提供商名'))
        dialog.key_edit.setPlaceholderText(i18n.tr('留空则用环境变量'))
        dialog.key_field.setMinimumWidth(240)
        dialog.base_edit.setMinimumWidth(240)
        dialog.base_edit.setPlaceholderText("http(s)://…/v1")
        self.address_row = _setting_row(i18n.tr('API 地址'), dialog.base_edit)
        self.protocol = style.ComboBox()
        self.protocol.addItem("OpenAI Chat Completions", "openai_compat")
        self.protocol_row = _setting_row(i18n.tr('API 协议'), self.protocol)
        for row in (self.provider_row, self.id_row, _setting_row(i18n.tr('名称'), self.name),
                    _setting_row(i18n.tr('API 密钥'), dialog.key_field),
                    self.address_row, self.protocol_row):
            fields.add_row(row)
        body.addWidget(fields)
        self.custom_settings = QPushButton(i18n.tr('使用其他 API 地址'))
        self.custom_settings.setCheckable(True)
        style.set_button(self.custom_settings, "plain", small=True)
        self.custom_settings.toggled.connect(lambda on: self.address_row.setVisible(
            on or self.kind.value() == "custom"))
        body.addWidget(self.custom_settings, 0, Qt.AlignmentFlag.AlignLeft)

        # 模型目录：一列模型，选中的就是这个连接的默认模型；「获取可用模型」整列替换。
        # 每个模型后面跟输入类型（文本 / 文本 + 视觉 / 未知，#48）：服务商目录声明过的
        # 标来源；没声明的显式「未知」，可点「测试视觉」花一次小图请求实测。
        models_head = QHBoxLayout()
        models_label = QLabel(i18n.tr('模型'))
        style.set_text_role(models_label, "section")
        models_head.addWidget(models_label, 1)
        style.set_button(dialog.fetch_button, "plain", small=True)
        models_head.addWidget(dialog.fetch_button)
        self.vision_probe_button = QPushButton(i18n.tr('测试视觉'))
        style.set_button(self.vision_probe_button, "plain", small=True)
        self.vision_probe_button.setToolTip(
            i18n.tr('给选中模型发一张极小的纯色方块图，实测它能否接收图像输入。'
                    '这是一次付费请求，只在这里点击时才会发送。'))
        self.vision_probe_button.setEnabled(False)
        self.vision_probe_button.clicked.connect(self._test_vision)
        models_head.addWidget(self.vision_probe_button)
        body.addLayout(models_head)
        self.model_list = QListWidget()
        self.model_list.setObjectName("modelCatalog")
        self.model_list.setMinimumHeight(132)
        self.model_list.currentItemChanged.connect(self._pick_model)
        body.addWidget(self.model_list)
        manual = QHBoxLayout()
        self.models = QLineEdit()
        self.models.setPlaceholderText(i18n.tr('手动添加模型（多个用逗号分隔）'))
        self.models.returnPressed.connect(self.add_model)
        add = QPushButton(i18n.tr('添加'))
        style.set_button(add, "secondary", small=True)
        add.clicked.connect(self.add_model)
        manual.addWidget(self.models, 1)
        manual.addWidget(add)
        body.addLayout(manual)
        body.addWidget(dialog.reasoning_row)
        body.addWidget(dialog.thinking_row)
        dialog.model_box.setParent(self.editor)
        dialog.model_box.hide()          # 数据模型：列表里看到的就是它的条目
        model = dialog.model_box.model()
        for signal in (model.rowsInserted, model.rowsRemoved, model.modelReset):
            signal.connect(lambda *_a: self._sync_model_list())
        dialog.model_box.currentTextChanged.connect(lambda _t: self._sync_model_list())
        # 换提供商（含目录↔自定义切换）后，上一家目录声明过的输入模态不再可信。
        dialog.provider_box.currentTextChanged.connect(self._provider_changed)
        # 密钥/地址变了，探测缓存的指纹就换了：标签要跟着回到真实的「未知」。
        dialog.key_edit.textChanged.connect(lambda _t: self._sync_model_list())
        dialog.base_edit.textChanged.connect(lambda _t: self._sync_model_list())
        dialog.test_button.setParent(self.editor)
        dialog.test_button.hide()
        body.addWidget(dialog.test_label)

        footer = QHBoxLayout()
        footer.addStretch(1)
        cancel = QPushButton(i18n.tr('取消'))
        cancel.clicked.connect(self.close_editor)
        self.cancel_button = cancel
        self.force_save_button = QPushButton(i18n.tr('仍然保存'))
        self.force_save_button.setVisible(False)
        self.force_save_button.clicked.connect(lambda: self.save() and self.close_editor())
        self.save_button = QPushButton(i18n.tr('测试并添加'))
        style.mark_primary(self.save_button)
        self.save_button.clicked.connect(self.test_and_save)
        footer.addWidget(cancel)
        footer.addWidget(self.force_save_button)
        footer.addWidget(self.save_button)
        body.addLayout(footer)

    # ---- 模型输入类型（#48）--------------------------------------------------------
    #: kind → 列表里跟在模型名后面的短语。未知是显式状态，不是省略。
    INPUT_LABELS = {"text": '文本', "text+image": '文本 + 视觉', "unknown": '未知'}

    def set_fetched_modalities(self, modalities: dict) -> None:
        """「获取可用模型」成功后，记下目录声明过的输入模态并刷新列表。"""
        self._catalog_modalities = {str(k): [str(m) for m in (v or [])]
                                    for k, v in (modalities or {}).items()}
        self._sync_model_list()

    def _editor_identity(self) -> tuple[str, str, str]:
        """编辑区当前的 provider / key / 地址（密钥含环境变量回退，与测试连接一致）。"""
        from folith.model_connections import environment_key
        provider = self.dialog.provider_box.currentText().strip()
        key = self.dialog.key_edit.text().strip() or environment_key(provider)
        return provider, key, self.dialog.base_edit.text().strip()

    def _model_input_view(self, name: str) -> dict:
        """一个模型的输入类型：目录声明优先，其次本机实测缓存，否则显式未知。

        不查任何内置模型清单——两处来源都是用户自己拿到的证据（拉过目录 / 点过
        测试），没有就如实显示「未知」。
        """
        declared = self._catalog_modalities.get(name)
        if declared is not None:
            return {"kind": "text+image" if "image" in declared else "text",
                    "source": "provider", "at": "", "message": ""}
        provider, key, base = self._editor_identity()
        probe = connection_status.vision_probe_result(provider, name, base, key)
        if probe:
            return {"kind": "text+image" if probe["ok"] else "text",
                    "source": "probe", "at": probe["at"], "message": probe["message"]}
        return {"kind": "unknown", "source": "", "at": "", "message": ""}

    def _input_tooltip(self, view: dict) -> str:
        if view["source"] == "provider":
            return i18n.tr('输入类型：{v0}（服务商模型目录声明）',
                           v0=i18n.tr(self.INPUT_LABELS[view["kind"]]))
        if view["source"] == "probe":
            stamp = view["at"].replace("T", " ")[:16]
            return i18n.tr('输入类型：{v0}（「测试视觉」实测 {v1}）',
                           v0=i18n.tr(self.INPUT_LABELS[view["kind"]]), v1=stamp)
        return i18n.tr('输入类型未知：服务商目录没有声明。可点「测试视觉」实测（一次小图请求）。')

    def _provider_changed(self, _text) -> None:
        """换了提供商：上一家目录声明过的输入模态不再可信，清掉重画。"""
        self._catalog_modalities = {}
        self._sync_model_list()

    def _pick_model(self, current, _previous) -> None:
        if current is None:
            self.vision_probe_button.setEnabled(False)
            return
        name = str(current.data(Qt.ItemDataRole.UserRole) or "")
        if name and self.dialog.model_box.currentText() != name:
            self.dialog.model_box.setCurrentText(name)
        self.vision_probe_button.setEnabled(not self._vision_probe_busy)

    def _test_vision(self) -> None:
        """对列表里选中的模型发一次极小图像请求（用户点按钮才会发生，#48）。"""
        from .dialogs import _set_status
        item = self.model_list.currentItem()
        model = str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""
        if not model:
            return
        provider, key, base = self._editor_identity()
        self._vision_probe_busy = True
        self.vision_probe_button.setEnabled(False)
        _set_status(self.dialog.test_label, i18n.tr('正在测试图像输入…'))

        def done(result) -> None:
            ok, message = result
            self._vision_probe_busy = False
            self.vision_probe_button.setEnabled(True)
            connection_status.record_vision_probe(provider, model, base, key,
                                                  bool(ok), str(message))
            _set_status(self.dialog.test_label, str(message), "ok" if ok else "danger")
            self._sync_model_list()
            self._update_vision_hint()

        def failed(message, _detail) -> None:
            self._vision_probe_busy = False
            self.vision_probe_button.setEnabled(True)
            _set_status(self.dialog.test_label, i18n.tr('图像测试失败：{v0}', v0=message), "danger")

        self.dialog.runner.submit(adapter.test_vision_model, provider, key, model, base,
                                  on_done=done, on_error=failed, label="test-vision-input")

    def _sync_model_list(self):
        box = self.dialog.model_box
        items = [box.itemText(i) for i in range(box.count())]
        current = box.currentText().strip()
        if current and current not in items:
            items.append(current)
        self.model_list.blockSignals(True)
        self.model_list.clear()
        for name in items:
            view = self._model_input_view(name)
            # 显示文本带输入类型，但选中回写用 UserRole 里的纯模型名：
            # model_box 的条目永远是干净的名字。
            item = QListWidgetItem(f"{name} · {i18n.tr(self.INPUT_LABELS[view['kind']])}")
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setToolTip(self._input_tooltip(view))
            self.model_list.addItem(item)
            if name == current:
                # 选中的就是默认模型：前面一个强调色对勾（画布上的单选点）。
                item.setIcon(icons.icon("check", 14, "accent"))
                item.setToolTip(i18n.tr('默认模型') + "\n" + item.toolTip())
                self.model_list.setCurrentItem(item)
        self.model_list.blockSignals(False)
        self.vision_probe_button.setEnabled(
            self.model_list.currentItem() is not None and not self._vision_probe_busy)

    def open_editor(self, key=""):
        if key:
            self.edit(key)
        else:
            self.reset()
        self._sync_model_list()
        self.force_save_button.setVisible(False)
        from .dialogs import _set_status
        _set_status(self.dialog.test_label, "")
        from . import bootstrap
        if not bootstrap.automation():
            self.editor.open()

    def embed_editor(self):
        """首次引导：面板不弹出，直接作为页面的一部分（没有标题和「取消」）。"""
        self._embedded = True
        self.editor.setWindowFlags(Qt.WindowType.Widget)
        self.editor_title.hide()
        self.cancel_button.hide()
        self.editor.show()

    def close_editor(self):
        if not getattr(self, "_embedded", False):
            self.editor.close()
        self.reset()

    def test_and_save(self):
        from .dialogs import _set_status
        profile = self._editor_profile(quiet=True)
        if profile is None:
            self._editor_profile()          # 把原因写到状态行
            _set_status(self.dialog.test_label, self.dialog._status.text(), "danger")
            return
        config = self.dialog._editor_config()
        self.save_button.setEnabled(False)
        _set_status(self.dialog.test_label, i18n.tr('正在测试连接…'))

        def done(result):
            ok, message = result
            self.save_button.setEnabled(True)
            if ok and self.save():
                self.close_editor()
                return
            _set_status(self.dialog.test_label, i18n.tr('连接失败，未保存：{v0}', v0=message), "danger")
            self.force_save_button.setVisible(True)

        def failed(message, _detail):
            self.save_button.setEnabled(True)
            _set_status(self.dialog.test_label, i18n.tr('连接失败，未保存：{v0}', v0=message), "danger")
            self.force_save_button.setVisible(True)

        self.dialog.runner.submit(adapter.test_connection, config, on_done=done,
                                  on_error=failed, label="test-new-connection")

    def _prefill_from_config(self):
        """还没有翻译连接时，编辑区先填上已有的设置（例如只填了密钥、还没选模型）。

        否则旧配置里的提供商、密钥和地址在新界面上看不到，一保存就丢了。
        """
        config = self.dialog.config
        if self.roles.get("translation") or not config.provider:
            return
        custom = config.provider == "自定义中转站"
        self.kind.set_value("custom" if custom else "catalog")
        self.change_kind("custom" if custom else "catalog")
        self.dialog.provider_box.setCurrentText(config.provider)
        if config.model:
            self.dialog.model_box.setCurrentText(config.model)
        self.dialog.key_edit.setText(config.api_key)
        self.dialog.base_edit.setText(config.base_url)
        self.custom_settings.setChecked(bool(config.base_url))

    def change_kind(self, kind):
        custom = kind == "custom"
        self.id_row.setVisible(custom)
        self.protocol_row.setVisible(custom)
        self.provider_row.setVisible(not custom)
        custom_index = self.dialog.provider_box.findText("自定义中转站")
        if custom_index >= 0:
            self.dialog.provider_box.model().item(custom_index).setEnabled(custom)
        self.custom_settings.setVisible(not custom)
        self.address_row.setVisible(custom or self.custom_settings.isChecked())
        if custom:
            self.dialog.provider_box.setCurrentText("自定义中转站")
        elif self.dialog.provider_box.currentText() == "自定义中转站":
            self.dialog.provider_box.setCurrentIndex(0)

    def reset(self):
        self.editing = ""
        self._pending_id = ""
        self._thinking_mode = ""
        self._catalog_modalities = {}
        self.dialog.thinking_box.setCurrentIndex(0)
        self.editor_title.setText(i18n.tr('添加连接'))
        self.save_button.setText(i18n.tr('测试并添加'))
        self.name.clear()
        self.provider_id.clear()
        self.models.clear()
        self.dialog.key_edit.clear()
        self.dialog.base_edit.clear()
        self.kind.set_value("catalog")
        self.custom_settings.setChecked(False)
        self.change_kind("catalog")

    def refresh(self):
        self.list_group.clear()
        self.list_group.add_row(self._group_header(i18n.tr('连接'), self.add_button))
        if not self.profiles:
            empty = QLabel(i18n.tr('还没有连接。点「添加连接」填写提供商和密钥。'))
            style.set_text_role(empty, "meta")
            holder = QWidget()
            row = QHBoxLayout(holder)
            row.setContentsMargins(theme_module.SPACE["md"], theme_module.SPACE["sm"],
                                   theme_module.SPACE["md"], theme_module.SPACE["md"])
            row.addWidget(empty)
            self.list_group.add_row(holder)
        for p in self.profiles:
            self.list_group.add_row(self._connection_row(p))
        for role, box in self.selectors.items():
            self.fill_selector(box, role)
        if hasattr(self.dialog, "vision_connection_box"):
            self.fill_selector(self.dialog.vision_connection_box, "vision")

    def _connection_row(self, p):
        """一行：圆点 · 名称（自定义标签）/ 提供商 · 模型 · 状态 · 编辑 · 删除。"""
        cfg = effective_config(self.dialog.config, [p], {"translation": p["id"]})
        cfg.model = p["model"]
        status = connection_status.status("translation", cfg)
        tone = self.STATE_TONE.get(status["state"], "idle")
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(theme_module.SPACE["md"], theme_module.SPACE["sm"],
                               theme_module.SPACE["sm"], theme_module.SPACE["sm"])
        row.setSpacing(theme_module.SPACE["md"])
        row.addWidget(components.StatusDot(theme_module.STATUS_TONES[tone]), 0,
                      Qt.AlignmentFlag.AlignVCenter)
        column = QVBoxLayout()
        column.setSpacing(0)
        title = QHBoxLayout()
        title.setSpacing(theme_module.SPACE["sm"])
        name = QLabel(p["name"])
        style.set_text_role(name, "name")
        title.addWidget(name)
        if p.get("custom"):
            title.addWidget(style.Chip(i18n.tr('自定义'), "muted"))
        title.addStretch(1)
        column.addLayout(title)
        models = list(dict.fromkeys([p["model"], *p.get("models", [])]))
        provider = i18n.tr('OpenAI 兼容') if p.get("custom") else i18n.tr_dynamic(p["provider"])
        detail = f"{provider} · {p['model']}"
        if len(models) > 1:
            detail = i18n.tr('{v0} 等 {v1} 个模型', v0=detail, v1=len(models))
        meta = style.ElideLabel(detail, mode=Qt.TextElideMode.ElideRight)
        style.set_text_role(meta, "meta")
        column.addWidget(meta)
        row.addLayout(column, 1)
        state = components.StatusLabel(i18n.tr(self.STATE_TEXT.get(status["state"], "")), tone)
        state.dot.hide()
        state.setToolTip(i18n.tr_dynamic(status["message"]))
        row.addWidget(state)
        edit = QPushButton(i18n.tr('编辑'))
        style.set_button(edit, "secondary", small=True)
        edit.clicked.connect(lambda _c=False, key=p["id"]: self.open_editor(key))
        # 删除是不可撤销的：危险色文字按钮 + 确认（默认焦点在取消上）。
        remove = QPushButton(i18n.tr('删除'))
        style.set_button(remove, "danger", small=True)
        remove.clicked.connect(lambda _c=False, key=p["id"], name=p["name"]: self.confirm_delete(key, name))
        row.addWidget(edit)
        row.addWidget(remove)
        return holder

    def fill_selector(self, box, role):
        box.blockSignals(True)
        box.clear()
        box.addItem(i18n.tr('选择连接') if role == "translation" else i18n.tr('沿用翻译模型'), "")
        for profile in self.profiles:
            if role == "vision" and profile["provider"] not in adapter.vision_providers():
                continue
            box.addItem(profile["name"], profile["id"])
        index = box.findData(self.roles.get(role, ""))
        box.setCurrentIndex(max(0, index))
        box.blockSignals(False)
        self.roles[role] = str(box.currentData() or "")
        self.update_model_selector(role)

    def make_model_selector(self, role):
        box = style.ComboBox()
        box.setSizeAdjustPolicy(box.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        box.setMinimumContentsLength(12)
        box.setAccessibleName(i18n.tr(connection_status.ROLE_LABELS[role]))
        box.currentTextChanged.connect(lambda value, r=role: self.role_models.update({r: value}))
        if role == "vision":
            # 换的不只是连接（select）时，模型本身变了也要重看输入类型。
            box.currentTextChanged.connect(lambda _v: self._update_vision_hint())
        # #258：换模型时刷新能力警告。
        box.currentTextChanged.connect(lambda model, r=role: self._update_capability_warning(r, model))
        self.model_selectors[role] = box
        return box

    def update_model_selector(self, role):
        box = self.model_selectors.get(role)
        if box is None:
            return
        profile = next((p for p in self.profiles if p["id"] == self.roles.get(role)), None)
        box.blockSignals(True)
        box.clear()
        if profile:
            models = list(dict.fromkeys([profile["model"], *profile.get("models", [])]))
            box.addItems(models)
            chosen = self.role_models.get(role, "")
            box.setCurrentText(chosen if chosen in models else profile["model"])
            self.role_models[role] = box.currentText()
        else:
            self.role_models[role] = ""
        box.setVisible(profile is not None)
        box.blockSignals(False)
        if role == "vision":
            self._update_vision_hint()
        self._update_capability_warning(role, box.currentText())

    def _update_vision_hint(self):
        """「视觉」用途当前选中的模型：目录声明 / 实测结果 / 未知，三态如实显示。

        已知仅文本用警告色；未知保持中性——没有证据就不下结论（#48）。
        """
        hint = getattr(self, "vision_modality_hint", None)
        if hint is None:
            return
        profile = next((p for p in self.profiles if p["id"] == self.roles.get("vision")), None)
        model = str(self.role_models.get("vision") or "")
        if not profile or not model:
            hint.setVisible(False)
            return
        declared = (profile.get("model_modalities") or {}).get(model)
        if isinstance(declared, list) and declared:
            if "image" in declared:
                hint.set_status(i18n.tr('服务商目录标注该模型支持图像输入'), "done")
            else:
                hint.set_status(i18n.tr('服务商目录标注该模型仅接受文本输入'), "attention")
            hint.setToolTip("")
            hint.setVisible(True)
            return
        probe = connection_status.vision_probe_result(
            str(profile.get("provider") or ""), model, str(profile.get("base_url") or ""),
            str(profile.get("api_key") or ""))
        if probe:
            if probe["ok"]:
                hint.set_status(i18n.tr('「测试视觉」实测支持图像输入'), "done")
            else:
                hint.set_status(i18n.tr('「测试视觉」实测未通过图像输入'), "attention")
                hint.setToolTip(probe["message"])
            hint.setVisible(True)
            return
        hint.set_status(i18n.tr('输入类型未知；可在编辑连接里点「测试视觉」实测'), "idle")
        hint.setToolTip("")
        hint.setVisible(True)

    def _update_capability_warning(self, role: str, model: str) -> None:
        """#258：选中的模型与用途不匹配时，在该行下面显示内联警告。"""
        warn = self.capability_warnings.get(role)
        if warn is None:
            return
        from .model_capability import capability_warning
        # 视觉用途的角色名对应 "vision"；翻译 / 审校对应各自名称。
        role_cat = "vision" if role == "vision" else role
        message = capability_warning(role_cat, model)
        if message:
            warn.set_status(i18n.tr_dynamic(message), "error")
            warn.setVisible(True)
        else:
            warn.setVisible(False)

    def select(self, role):
        box = self.selectors.get(role) if role != "vision" else self.dialog.vision_connection_box
        self.roles[role] = str(box.currentData() or "")
        self.role_models.pop(role, None)
        self.update_model_selector(role)

    def add_model(self):
        """手动添加模型：加进目录并选中最后一个。"""
        box = self.dialog.model_box
        values = [m.strip() for m in self.models.text().split(",") if m.strip()]
        for value in values:
            if box.findText(value) < 0:
                box.addItem(value)
        if values:
            box.setCurrentText(values[-1])
        self.models.clear()

    def edit(self, key):
        p = next(p for p in self.profiles if p["id"] == key)
        self.editing = key
        self._thinking_mode = str(p.get("reasoning_mode") or "")
        self.editor_title.setText(i18n.tr('编辑连接'))
        self.save_button.setText(i18n.tr('测试并保存'))
        self.kind.set_value("custom" if p.get("custom") else "catalog")
        self.change_kind("custom" if p.get("custom") else "catalog")
        self.provider_id.setText(p.get("provider_id", ""))
        self.name.setText(p["name"])
        self.dialog.provider_box.setCurrentText(p["provider"])
        # 目录声明跟着这条连接的模型走：编辑已保存连接时先恢复它上次的目录信息
        # （provider 切换信号刚清空过，这里在模型填进去之后重新赋值）。
        self._catalog_modalities = {
            str(m): [str(x) for x in (v or [])]
            for m, v in (p.get("model_modalities") or {}).items()}
        # 密钥/地址要先落位再触发列表刷新：探测缓存按 连接+模型 指纹查，
        # 刷新发生在密钥还是空的时候会把「实测过」错画成「未知」。
        self.dialog.key_edit.setText(p.get("api_key", ""))
        self.dialog.base_edit.setText(p.get("base_url", ""))
        self.dialog.model_box.addItems([m for m in p.get("models", [])
                                       if self.dialog.model_box.findText(m) < 0])
        self.dialog.model_box.setCurrentText(p["model"])
        self.custom_settings.setChecked(bool(p.get("base_url")))
        self.dialog.reasoning_box.setCurrentIndex(max(0,
            self.dialog.reasoning_box.findData(p.get("reasoning_effort", ""))))
        self.dialog.thinking_box.setCurrentIndex(max(0,
            self.dialog.thinking_box.findData(p.get("reasoning_mode", ""))))

    def _editor_profile(self, quiet=False):
        """把编辑区读成一条连接；填得不完整时返回 None（``quiet`` 时不写状态）。"""
        from .dialogs import _set_status

        def fail(message):
            if not quiet:
                _set_status(self.dialog._status, message, "danger")

        custom = self.kind.value() == "custom"
        provider_id = self.provider_id.text().strip()
        if custom and (not provider_id or any(p.get("provider_id") == provider_id
                and p["id"] != self.editing for p in self.profiles)):
            fail(i18n.tr('提供商 ID 必须填写且不能重复'))
            return None
        if not self.editing and not self._pending_id:
            self._pending_id = uuid.uuid4().hex
        p = {"id": self.editing or self._pending_id,
             "name": self.name.text().strip() or self.dialog.provider_box.currentText(),
             "provider_id": provider_id, "custom": custom,
             "reasoning_effort": (str(self.dialog.reasoning_box.currentData() or "")
                                  if self.dialog.reasoning_box.count() > 1
                                  else self.dialog.config.reasoning_effort),
             "reasoning_mode": (str(self.dialog.thinking_box.currentData() or "")
                                if self.dialog.thinking_box.count() > 1
                                else self._thinking_mode),
             "provider": self.dialog.provider_box.currentText(),
             "model": self.dialog.model_box.currentText().strip(),
             "api_key": self.dialog.key_edit.text().strip(),
             "base_url": self.dialog.base_edit.text().strip(),
             "protocol": "openai_compat" if custom else adapter.core.PROVIDERS[
                 self.dialog.provider_box.currentText()]["kind"]}
        box = self.dialog.model_box
        typed = [m.strip() for m in self.models.text().split(",") if m.strip()]
        p["models"] = list(dict.fromkeys(
            [box.itemText(i) for i in range(box.count())] + typed + [p["model"]]))
        # #48：目录声明过的输入模态随连接保存；「测试视觉」的实测结果不进配置——
        # 那是本机观测，在 connection_status 的缓存里按配置指纹失效。
        p["model_modalities"] = {m: [str(x) for x in (v or [])]
                                 for m, v in self._catalog_modalities.items()
                                 if m in p["models"]}
        if not p["reasoning_mode"]:
            # 这个模型没有经过验证的思考控件：不写档位，而不是写一个"服务默认"。
            # 否则任何一次保存都会替连接记下显式默认值，把 FOLITH_REASONING_MODE
            # 这个出口一起盖掉。
            del p["reasoning_mode"]
        if not p["model"] or (custom and not p["base_url"]):
            fail(i18n.tr('请填写模型和 API 地址'))
            return None
        try:
            if p["base_url"]:
                p["base_url"] = adapter.core.normalize_openai_base_url(p["base_url"])
            adapter.core.resolve_openai_base_url(p["provider"], p["base_url"])
        except ValueError as exc:
            fail(str(exc))
            return None
        return p

    def pending_profile(self):
        """编辑区里看得见、但还没单独「保存」的那条连接；没有时返回 None。

        两种情况算数：正在编辑一条已保存的连接（改动以编辑区为准），或者还没有
        翻译连接、编辑区已经填好（它就是要用的那条）。页面上的保存 / 测试并保存
        都按用户眼前看到的内容来，不会悄悄丢掉编辑区里的改动。
        """
        if not self.editing and self.roles.get("translation"):
            return None
        return self._editor_profile(quiet=True)

    def adopt(self, config):
        """「测试并保存」落盘之后，列表跟上已保存的连接，编辑区清空。"""
        staged = any(p["id"] in (self._pending_id, self.editing) for p in config.connections
                     if p["id"])
        self.profiles = [dict(p) for p in config.connections]
        self.roles = dict(config.connection_roles)
        self.role_models = dict(config.connection_models)
        self.refresh()
        if staged:
            self.reset()

    def save(self):
        p = self._editor_profile()
        if p is None:
            return False
        old = self.profiles
        old_roles = dict(self.roles)
        old_models = dict(self.role_models)
        self.profiles = [p if c["id"] == p["id"] else c for c in old]
        if not self.editing:
            self.profiles.append(p)
        if not self.roles.get("translation"):
            self.roles["translation"] = p["id"]
        for role in self.roles:
            self.update_model_selector(role)
        if self.persist():
            self.refresh()
            self.reset()
            return True
        self.profiles = old
        self.roles = old_roles
        self.role_models = old_models
        return False

    def persist(self):
        from .dialogs import _set_status
        import dataclasses
        base = dataclasses.replace(self.dialog.config, connection_models=dict(self.role_models))
        cfg = effective_config(base, self.profiles, self.roles)
        try:
            # Keep environment-derived credentials out of the saved profile.
            self.dialog._apply_vision()
            cfg.save()
        except Exception as exc:
            _set_status(self.dialog._status, str(exc), "danger")
            return False
        self.dialog.config = cfg
        self.dialog.connectionApplied.emit()
        _set_status(self.dialog._status, i18n.tr('已保存'), "ok")
        return True

    def confirm_delete(self, key, name):
        from . import bootstrap, prompts

        if not bootstrap.automation() and not prompts.ConfirmDialog.ask(
                self.dialog, i18n.tr('删除连接'),
                i18n.tr('删除「{v0}」？用到它的用途会改为未选择，密钥一并删除。', v0=name),
                yes=i18n.tr('删除'), no=i18n.tr('取消'), tone="danger"):
            return
        self.delete(key)

    def delete(self, key):
        old = self.profiles
        old_roles = dict(self.roles)
        self.roles = {role: "" if selected == key else selected
                      for role, selected in self.roles.items()}
        self.profiles = [p for p in old if p["id"] != key]
        if self.persist():
            self.refresh()
            if self.editing == key:
                self.reset()
        else:
            self.profiles = old
            self.roles = old_roles
