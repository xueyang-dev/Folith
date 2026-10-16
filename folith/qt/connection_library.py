"""Saved local connections and role selection for the settings window."""
from __future__ import annotations

import uuid
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLineEdit
from . import adapter, components, connection_status, i18n, style
from folith.model_connections import effective_config


class ConnectionLibrary(QWidget):
    def __init__(self, dialog):
        super().__init__()
        from .dialogs import _panel, _field
        self.dialog = dialog
        self.profiles = [dict(p) for p in dialog.config.connections]
        self.roles = dict(dialog.config.connection_roles)
        self.role_models = dict(dialog.config.connection_models)
        self.model_selectors = {}
        self.editing = ""
        #: 编辑区里尚未保存的那条连接的 id：多次读取编辑区时保持不变。
        self._pending_id = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.saved = QVBoxLayout()
        layout.addLayout(self.saved)
        role_card, body = _panel(i18n.tr('模型用途'))
        self.selectors = {}
        for role in ("translation", "reviewer"):
            box = style.ComboBox()
            box.setSizeAdjustPolicy(box.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            box.setMinimumContentsLength(12)
            box.currentIndexChanged.connect(lambda _i, r=role: self.select(r))
            model_box = self.make_model_selector(role)
            holder = QWidget()
            row = QHBoxLayout(holder)
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(box, 1)
            row.addWidget(model_box, 1)
            body.addWidget(_field(i18n.tr(connection_status.ROLE_LABELS[role]), holder))
            self.selectors[role] = box
        body.addWidget(dialog._apply_row("models"))
        layout.addWidget(role_card)
        self.editor, body = _panel(i18n.tr('添加连接'))
        self.kind = style.SegmentedControl([
            ("catalog", i18n.tr('第三方模型提供商')), ("custom", i18n.tr('自定义模型 API'))])
        self.kind.set_value("catalog")
        self.kind.changed.connect(self.change_kind)
        body.addWidget(self.kind)
        self.provider_id = QLineEdit()
        self.id_row = _field(i18n.tr('提供商 ID'), self.provider_id)
        body.addWidget(self.id_row)
        self.name = QLineEdit()
        body.addWidget(_field(i18n.tr('显示名称'), self.name))
        # The existing editor widgets keep connection testing/model discovery on
        # the same adapter path as actual translation.
        self.provider_row = _field(i18n.tr('模型提供商'), dialog.provider_box)
        body.addWidget(self.provider_row)
        body.addWidget(_field(i18n.tr('API 密钥'), dialog.key_field))
        dialog.key_edit.setPlaceholderText(i18n.tr('留空使用环境变量认证'))
        self.custom_settings = QPushButton(i18n.tr('自定义设置'))
        self.custom_settings.setCheckable(True)
        self.address_row = _field(i18n.tr('API 地址'), dialog.base_edit)
        self.custom_settings.toggled.connect(self.address_row.setVisible)
        body.addWidget(self.custom_settings)
        body.addWidget(self.address_row)
        self.protocol = style.ComboBox()
        self.protocol.addItem("OpenAI Chat Completions", "openai_compat")
        self.protocol_row = _field(i18n.tr('API 协议'), self.protocol)
        body.addWidget(self.protocol_row)
        row = QHBoxLayout()
        row.addWidget(dialog.model_box, 1)
        row.addWidget(dialog.fetch_button)
        body.addWidget(style.field_label(i18n.tr('模型目录')))
        body.addLayout(row)
        self.models = QLineEdit()
        self.models.setPlaceholderText(i18n.tr('多个模型用逗号分隔'))
        body.addWidget(self.models)
        add = QPushButton(i18n.tr('添加模型'))
        add.clicked.connect(self.add_model)
        body.addWidget(add)
        body.addWidget(dialog.reasoning_row)
        body.addWidget(dialog.test_button)
        body.addWidget(dialog.test_label)
        actions = QHBoxLayout()
        cancel = QPushButton(i18n.tr('取消'))
        cancel.clicked.connect(self.reset)
        save = QPushButton(i18n.tr('保存'))
        style.mark_primary(save)
        save.clicked.connect(self.save)
        actions.addStretch()
        actions.addWidget(cancel)
        actions.addWidget(save)
        body.addLayout(actions)
        layout.addWidget(self.editor)
        self.refresh()
        self.reset()
        self._prefill_from_config()

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
        self.editor.layout().itemAt(0).widget().setText(i18n.tr('添加连接'))
        self.name.clear()
        self.provider_id.clear()
        self.models.clear()
        self.dialog.key_edit.clear()
        self.dialog.base_edit.clear()
        self.kind.set_value("catalog")
        self.custom_settings.setChecked(False)
        self.change_kind("catalog")

    def refresh(self):
        from .dialogs import _panel
        while self.saved.count():
            item = self.saved.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for p in self.profiles:
            cfg = effective_config(self.dialog.config, [p], {"translation": p["id"]})
            cfg.model = p["model"]
            status = connection_status.status("translation", cfg)
            card, body = _panel("")
            body.takeAt(0).widget().deleteLater()
            row = QHBoxLayout()
            label = components.StatusLabel()
            label.set_status(p["name"] + (" · " + i18n.tr('自定义') if p.get("custom") else ""),
                             "done" if status["state"] == "ok" else "idle")
            label.setToolTip(i18n.tr_dynamic(status["message"]))
            row.addWidget(label, 1)
            edit = QPushButton(i18n.tr('编辑'))
            style.set_button(edit, "secondary", small=True)
            edit.clicked.connect(lambda _c=False, key=p["id"]: self.edit(key))
            # 删除是不可撤销的：危险色文字按钮 + 确认（默认焦点在取消上）。
            remove = QPushButton(i18n.tr('删除'))
            style.set_button(remove, "danger", small=True)
            remove.clicked.connect(lambda _c=False, key=p["id"], name=p["name"]: self.confirm_delete(key, name))
            row.addWidget(edit)
            row.addWidget(remove)
            body.addLayout(row)
            self.saved.addWidget(card)
        for role, box in self.selectors.items():
            self.fill_selector(box, role)
        if hasattr(self.dialog, "vision_connection_box"):
            self.fill_selector(self.dialog.vision_connection_box, "vision")

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

    def select(self, role):
        box = self.selectors.get(role) if role != "vision" else self.dialog.vision_connection_box
        self.roles[role] = str(box.currentData() or "")
        self.role_models.pop(role, None)
        self.update_model_selector(role)

    def add_model(self):
        values = [m.strip() for m in self.models.text().split(",") if m.strip()]
        model = self.dialog.model_box.currentText().strip()
        if model and model not in values:
            values.append(model)
        self.models.setText(", ".join(values))

    def edit(self, key):
        p = next(p for p in self.profiles if p["id"] == key)
        self.editing = key
        self.editor.layout().itemAt(0).widget().setText(i18n.tr('编辑连接'))
        self.kind.set_value("custom" if p.get("custom") else "catalog")
        self.change_kind("custom" if p.get("custom") else "catalog")
        self.provider_id.setText(p.get("provider_id", ""))
        self.name.setText(p["name"])
        self.dialog.provider_box.setCurrentText(p["provider"])
        self.dialog.model_box.addItems([m for m in p.get("models", [])
                                       if self.dialog.model_box.findText(m) < 0])
        self.dialog.model_box.setCurrentText(p["model"])
        self.dialog.key_edit.setText(p.get("api_key", ""))
        self.dialog.base_edit.setText(p.get("base_url", ""))
        self.custom_settings.setChecked(bool(p.get("base_url")))
        self.models.setText(", ".join(p.get("models") or [p["model"]]))
        self.dialog.reasoning_box.setCurrentIndex(max(0,
            self.dialog.reasoning_box.findData(p.get("reasoning_effort", ""))))

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
             "provider": self.dialog.provider_box.currentText(),
             "model": self.dialog.model_box.currentText().strip(),
             "api_key": self.dialog.key_edit.text().strip(),
             "base_url": self.dialog.base_edit.text().strip(),
             "protocol": "openai_compat" if custom else adapter.core.PROVIDERS[
                 self.dialog.provider_box.currentText()]["kind"]}
        p["models"] = list(dict.fromkeys([m.strip() for m in self.models.text().split(",") if m.strip()] + [p["model"]]))
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
            return
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
        else:
            self.profiles = old
            self.roles = old_roles
            self.role_models = old_models

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
