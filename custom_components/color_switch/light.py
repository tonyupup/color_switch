import logging
from datetime import datetime
import asyncio

import voluptuous as vol

from homeassistant.components.light import LightEntity, ColorMode, LightEntityFeature
from homeassistant.const import STATE_ON, STATE_OFF, ATTR_ENTITY_ID, CONF_NAME
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.core import callback
from .const import (
    CONF_QUICK_TOGGLE_WINDOW,
    DEFAULT_COLORS,
    CONF_COLORS,
    CONF_SWITCHES,
    CONF_QUICK_TOGGLE_TIME,
)
from asyncio.locks import Lock


_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the color switch platform from a config entry."""
    data = config_entry.data

    # 为每个配置的开关创建实体
    switches = (
        ThreeColorLight(
            hass=hass,
            switch_entity_id=switch_entity_id,
            effect_index=DEFAULT_COLORS.index(current_color),
            quick_toggle_window=data[CONF_QUICK_TOGGLE_WINDOW],
            toggle_time=data[CONF_QUICK_TOGGLE_TIME],
        )
        for switch_entity_id, current_color in data[CONF_SWITCHES].items()
    )
    async_add_entities(switches)


class ThreeColorLight(LightEntity):
    """Light entity that cycles through warm white, white, and yellow on quick toggles."""

    def __init__(
        self,
        hass,
        switch_entity_id: str,
        effect_index,
        quick_toggle_window,
        toggle_time,
    ):
        """Initialize the light with a name and the switch entity ID."""
        self._attr_supported_features = LightEntityFeature.EFFECT
        self._hass = hass
        self._name = f"Three Color {switch_entity_id}"
        self._switch_entity_id = switch_entity_id
        self._is_on = False
        self._rotating = False
        self._last_off_time = None
        self._attr_unique_id = f"three_color_switch_{switch_entity_id}"
        self._attr_effect_index = effect_index
        self._quick_toggle_window = quick_toggle_window
        self._toggle_time = toggle_time
        self._effect_list = DEFAULT_COLORS

    @property
    def name(self):
        """Return the name of the light."""
        return self._name

    @property
    def is_on(self):
        """Return True if light is on."""
        return self._is_on

    @property
    def color_mode(self):
        """Return the current color mode (RGB)."""
        return ColorMode.ONOFF

    @property
    def effect_list(self):
        """返回可用的效果名称列表给 UI 选择。"""
        return list(self._effect_list)

    @property
    def supported_color_modes(self):
        """Return set containing supported color modes (only RGB, no brightness)."""
        return {ColorMode.ONOFF}

    @property
    def effect(self):
        """返回当前选中的效果名称。"""
        return self._effect_list[self._effect_index] if self._is_on else None

    @property
    def brightness(self):
        """Return fixed brightness (always full when on)."""
        return 255 if self._is_on else None

    async def _rotate_color(self, target_effect_index: int):
        """通过快速开关切换颜色."""

        if self._rotating:
            return
            # 如果灯当前是关的，得开了
        if self.state == STATE_OFF:
            # 打开开关
            await self._hass.services.async_call(
                "switch", "turn_on", {"entity_id": self._switch_entity_id}
            )
        self._rotating = True
        original_effect_index = self._attr_effect_index

        press_count = 0
        # 计算需要切换的次数
        # 从当前颜色到目标颜色需要切换多少次
        if target_effect_index > original_effect_index:
            press_count = target_effect_index - original_effect_index
        else:
            press_count = (
                len(self._effect_list) - original_effect_index + target_effect_index
            )

        _LOGGER.debug(
            "Rotating color from %s to %s, requires %d presses",
            self._effect_list[original_effect_index],
            self._effect_list[target_effect_index],
            press_count,
        )

        if press_count == 0:
            return

        # 执行快速开关操作
        for i in range(press_count):
            # 关闭开关
            await self._hass.services.async_call(
                "switch", "turn_off", {"entity_id": self._switch_entity_id}
            )
            await asyncio.sleep(self._toggle_time)  # 短暂延迟

            # 打开开关
            await self._hass.services.async_call(
                "switch", "turn_on", {"entity_id": self._switch_entity_id}
            )

            # 如果不是最后一次操作，等待更长时间
            if i < press_count - 1:
                await asyncio.sleep(self._toggle_time)  # 操作间延迟

        # 更新颜色状态
        self._effect_index = target_effect_index
        self._rotating = False
        self._is_on = True
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs):
        """
        处理 turn_on 调用：
        - effect 参数存在时：切换到对应效果（颜色）；
        - 无 effect 且灯关：调用物理 switch 打开；
        - 无 effect 且灯开：UI 上循环颜色。
        """
        # 用户指定了效果
        if effect := kwargs.get("effect"):
            if effect in self._effect_list:
                idx = self._effect_list.index(effect)
                _LOGGER.info("Set effect → %s (index %d)", effect, idx)
                return await self._rotate_color(idx)
            _LOGGER.warning("Unknown effect %s", effect)
            return None
        if rgb_color := kwargs.get("rgb_color"):
            if rgb_color in self._colors:
                idx = self._effect_list.index(effect)
                _LOGGER.info("Set color → %s (index %d)", rgb_color, idx)
                return await self._rotate_color(idx)

        # 灯当前关闭：走物理开关
        if not self._is_on:
            _LOGGER.debug("UI/Service: Turning physical switch on")
            await self._hass.services.async_call(
                "switch",
                "turn_on",
                {ATTR_ENTITY_ID: self._switch_entity_id},
                blocking=True,
            )
        return None

    async def async_turn_off(self, **kwargs):
        """Handle turning off the light. Delegates to the physical switch."""
        if not self._is_on:
            # Already off; nothing to do
            return
        _LOGGER.debug("Turning off via switch %s", self._switch_entity_id)
        await self._hass.services.async_call(
            "switch",
            "turn_off",
            {ATTR_ENTITY_ID: self._switch_entity_id},
            blocking=True,
        )
        # The state change callback will update _is_on and record the off time

    async def async_added_to_hass(self):
        """Subscribe to switch state changes when entity is added."""
        await super().async_added_to_hass()
        # Track state changes of the physical switch
        self.async_on_remove(
            async_track_state_change_event(
                self._hass, self._switch_entity_id, self._async_switch_changed
            )
        )
        # Initialize state based on current switch status
        switch_state = self._hass.states.get(self._switch_entity_id)
        if switch_state and switch_state.state == STATE_ON:
            self._is_on = True
        self.async_write_ha_state()

    @callback
    def _async_switch_changed(self, event):
        """Handle state changes of the physical switch."""
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if new_state is None or new_state.state == old_state.state:
            return  # No actual change
        now = datetime.now()

        if new_state.state == STATE_OFF:
            # Record time when switch was turned off
            self._is_on = False
            self._last_off_time = now
            _LOGGER.debug("Switch off detected, recording time %s", now)
        elif new_state.state == STATE_ON:
            # When turned on, check if it was a quick toggle
            if self._last_off_time:
                elapsed = (now - self._last_off_time).total_seconds()
                _LOGGER.debug("Switch on detected after %.1fs", elapsed)
                if elapsed < self._quick_toggle_window:
                    # Cycle to next color index
                    self._attr_effect_index = (self._attr_effect_index + 1) % len(
                        self._effect_list
                    )
                    _LOGGER.info(
                        "Quick toggle: changing effect index to %d", self._attr_effect_index
                    )
            self._is_on = True

        # Update Home Assistant state
        if not self._rotating:
            self.async_write_ha_state()
