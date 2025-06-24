# custom_components/color_switch/config_flow.py

import logging
import voluptuous as vol
from collections import defaultdict
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import (
    selector,
    entity_registry as er,
    device_registry as dr,
    area_registry as ar,
)
from .const import (
    DOMAIN,
    CONF_SWITCHES,
    CONF_COLORS,
    CONF_QUICK_TOGGLE_WINDOW,
    CONF_QUICK_TOGGLE_TIME,
    DEFAULT_COLORS,
    DEFAULT_QUICK_TOGGLE_WINDOW,
    DEFAULT_QUICK_TOGGLE_TIME,
    ACTION_PREV,
    ACTION_DONE,
    ACTION_NEXT,
    CONF_ACTION,
)

_LOGGER = logging.getLogger(__name__)
PAGE_SIZE = 10  # 每页显示的开关数量


class ColorSwitchConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Color Switch with detailed switch information."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

    # 创建操作选项
    action_options = [
        ACTION_PREV,
        ACTION_NEXT,
        ACTION_DONE,
    ]

    def __init__(self):
        """Initialize the config flow."""
        self._all_switches = []  # 存储所有开关信息
        self._init_colors = {}  # 存储每个开关的初始颜色
        self._selected_switches = defaultdict(str)
        self._colors = DEFAULT_COLORS
        self._quick_toggle_window = DEFAULT_QUICK_TOGGLE_WINDOW
        self._quick_toggle_time = DEFAULT_QUICK_TOGGLE_TIME
        self._current_page = 0  # 当前页码
        self._total_pages = 0  # 总页数

    async def async_step_user(self, user_input=None):
        """Handle the initial step - load all switches."""
        # 获取注册表
        entity_reg = er.async_get(self.hass)
        device_reg = dr.async_get(self.hass)
        area_reg = ar.async_get(self.hass)

        # 收集所有可用开关
        self._all_switches = []
        for entity_id in self.hass.states.async_entity_ids("switch"):
            # 检查实体是否被禁用
            registry_entry = entity_reg.async_get(entity_id)
            if registry_entry and registry_entry.disabled:
                continue

            # 获取实体信息
            state = self.hass.states.get(entity_id)
            friendly_name = (
                state.attributes.get("friendly_name", entity_id) if state else entity_id
            )

            # 获取设备信息
            device_info = ""
            if registry_entry and registry_entry.device_id:
                device = device_reg.async_get(registry_entry.device_id)
                if device:
                    device_info = (
                        device.name_by_user or device.name or device.model or ""
                    )

            # 获取区域信息
            area_info = ""
            if registry_entry and registry_entry.area_id:
                area = area_reg.async_get_area(registry_entry.area_id)
                if area:
                    area_info = area.name

            # 存储开关信息
            self._all_switches.append(
                {
                    "entity_id": entity_id,
                    "friendly_name": friendly_name,
                    "device": device_info,
                    "area": area_info,
                }
            )

        # 按区域和设备分组排序
        self._all_switches.sort(
            key=lambda x: (
                x["area"] or "未分配区域",
                x["device"] or "未分配设备",
                x["friendly_name"],
            )
        )

        # 计算总页数
        self._total_pages = (len(self._all_switches) + PAGE_SIZE - 1) // PAGE_SIZE
        self._current_page = 0

        # 初始化每个开关的初始颜色
        for switch in self._all_switches:
            entity_id = switch["entity_id"]
            if entity_id not in self._init_colors:
                self._init_colors[entity_id] = self._colors[0] if self._colors else ""

        return await self.async_step_show_switches()

    def get_switch_selected_schema(self):
        # 计算当前页的开关
        start_idx = self._current_page * PAGE_SIZE
        end_idx = min(start_idx + PAGE_SIZE, len(self._all_switches))
        current_page_switches = self._all_switches[start_idx:end_idx]

        # 创建开关选项列表
        switch_options = []
        for switch in current_page_switches:
            # 创建详细标签
            label_parts = []
            if switch["area"]:
                label_parts.append(f"区域: {switch['area']}")
            if switch["device"]:
                label_parts.append(f"设备: {switch['device']}")

            details = f" - {' | '.join(label_parts)}" if label_parts else ""

            # 检查是否已选择
            prefix = "✓ " if switch["entity_id"] in self._selected_switches else ""

            switch_options.append(
                {
                    "value": switch["entity_id"],
                    "label": f"{prefix:<20}{switch['friendly_name']}{details}",
                }
            )

        # 创建基础Schema
        schema = vol.Schema(
            {
                vol.Required(CONF_SWITCHES, default=[]): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=switch_options,
                        multiple=True,
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ),
                vol.Required(CONF_ACTION, default=ACTION_NEXT): vol.In(
                    self.action_options
                ),
            },
        )

        # 为每个开关添加颜色选项
        color_fields = {}
        for switch in current_page_switches:
            entity_id = switch["entity_id"]
            color_fields[
                vol.Optional(
                    f"initial_color_{entity_id}",
                    default=self._init_colors.get(
                        entity_id, self._colors[0] if self._colors else ""
                    ),
                )
            ] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=self._colors,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )

        # 合并Schema
        return schema.extend(color_fields)

    async def async_step_show_switches(self, user_input=None):
        """Show switches for current page."""
        errors = {}

        if user_input is not None:
            # 保存选择的开关
            self._selected_switches.update(
                dict.fromkeys(user_input[CONF_SWITCHES], DEFAULT_COLORS[0])
            )

            # 保存当前页的初始颜色设置
            for switch in self._all_switches[
                self._current_page * PAGE_SIZE : (self._current_page + 1) * PAGE_SIZE
            ]:
                entity_id = switch["entity_id"]
                color_key = f"initial_color_{entity_id}"
                if color_key in user_input:
                    self._init_colors[entity_id] = user_input[color_key]

            action = user_input[CONF_ACTION]

            if action == ACTION_PREV:
                if self._current_page > 0:
                    self._current_page -= 1
            elif action == ACTION_NEXT:
                if self._current_page < self._total_pages - 1:
                    self._current_page += 1
            elif action == ACTION_DONE:
                # 确保至少选择了一个开关
                if not self._selected_switches:
                    errors["base"] = "no_switches_selected"
                else:
                    return await self.async_step_configure_options()

        # 重新生成Schema以反映当前状态
        schema = self.get_switch_selected_schema()

        return self.async_show_form(
            step_id="show_switches", data_schema=schema, errors=errors
        )

    async def async_step_configure_options(self, user_input=None):
        """Handle configuration options and create entry."""
        errors = {}

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_QUICK_TOGGLE_WINDOW, default=self._quick_toggle_window
                ): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=5.0)),
                vol.Required(
                    CONF_QUICK_TOGGLE_TIME, default=self._quick_toggle_time
                ): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=5.0)),
            }
        )
        if user_input is None:
            # 第一次显示配置选项
            return self.async_show_form(
                step_id="configure_options",
                data_schema=schema,
            )

        # 处理用户输入
        self._quick_toggle_window = user_input[CONF_QUICK_TOGGLE_WINDOW]
        self._quick_toggle_time = user_input[CONF_QUICK_TOGGLE_TIME]

        if errors:
            return self.async_show_form(
                step_id="configure_options",
                data_schema=schema,
                errors=errors,
            )

        for switch in self._selected_switches:
            self._selected_switches[switch] = self._init_colors[switch]
        # 创建配置项
        config_data = {
            CONF_SWITCHES: self._selected_switches,
            CONF_QUICK_TOGGLE_WINDOW: self._quick_toggle_window,
            CONF_QUICK_TOGGLE_TIME: self._quick_toggle_time,
        }

        return self.async_create_entry(
            title=f"彩色开关 ({len(self._selected_switches)}个设备)", data=config_data
        )
