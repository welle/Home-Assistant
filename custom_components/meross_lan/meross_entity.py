"""
 Base-Common behaviour for all Meross-LAN entities

 actual HA custom platform entities will be derived like this:
 MLSwitch(MerossToggle, SwitchEntity)

 we also try to 'commonize' HA core symbols import in order to better manage
 versioning
"""

from __future__ import annotations

import typing

from homeassistant import const as hac
from homeassistant.helpers.entity import Entity, EntityCategory

from .helpers import Loggable
from .helpers.manager import ApiProfile
from .merossclient import NAMESPACE_TO_KEY, const as mc

if typing.TYPE_CHECKING:
    from typing import ClassVar, Final

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .helpers.manager import EntityManager
    from .helpers.namespaces import NamespaceHandler
    from .meross_device import MerossDeviceBase


class MerossEntity(Loggable, Entity if typing.TYPE_CHECKING else object):
    """
    Mixin style base class for all of the entity platform(s)
    This class must prepend the HA entity class in our custom
    entity classe definitions like:
    from homeassistant.components.switch import Switch
    class MyCustomSwitch(MerossEntity, Switch)
    """

    PLATFORM: ClassVar[str]

    EntityCategory = EntityCategory

    is_diagnostic: ClassVar[bool] = False
    """Tells if this entity has been created as part of the 'create_diagnostic_entities' config"""

    # These 'placeholder' definitions support generalization of
    # Meross protocol message build/parsing when related to the
    # current entity. These are usually relevant when this entity
    # is strictly related to a namespace payload key value.
    # See MLConfigNumber or MerossToggle as basic implementations
    # supporting this semantic. They're generally set as class definitions
    # in inherited entities but could nonetheless be set 'per instance'.
    # These also come handy when generalizing parsing of received payloads
    # for simple enough entities (like sensors, numbers or switches)
    namespace: str
    key_namespace: str
    key_channel: str = mc.KEY_CHANNEL
    key_value: str

    # HA core entity attributes:
    # These are constants throughout our model
    force_update: Final[bool] = False
    has_entity_name: Final[bool] = True
    should_poll: Final[bool] = False
    # These may be customized here and there per class
    _attr_available: ClassVar[bool] = False
    # These may be customized here and there per class or instance
    assumed_state: bool = False
    entity_category: EntityCategory | None = None
    entity_registry_enabled_default: bool = True
    extra_state_attributes: dict[str, object] = {}
    icon: str | None = None
    translation_key: str | None = None
    # These are actually per instance
    available: bool
    device_class: Final[object | str | None]
    name: str | None
    suggested_object_id: str | None
    unique_id: str

    # used to speed-up checks if entity is enabled and loaded
    _hass_connected: bool

    __slots__ = (
        "manager",
        "channel",
        "entitykey",
        "namespace_handlers",
        "available",
        "device_class",
        "name",
        "suggested_object_id",
        "unique_id",
        "_hass_connected",
    )

    def __init__(
        self,
        manager: EntityManager,
        channel: object | None,
        entitykey: str | None = None,
        device_class: object | str | None = None,
    ):
        """
        - channel: historically used to create an unique id for this entity inside the device
        and also related to the physical channel used in various api for some kind of entities.
        For entities in subdevices (hub paired devices) the channel is usually the Id of the
        subdevice itself since 'HA wise' and 'meross_lan wise' we still group the entities under
        the same (hub) device
        - entitykey: is added to provide additional 'uniqueness' should the device have multiple
        entities for the same channel and usually equal to device_class (but might not be)
        - device_class: used by HA to set some soft 'class properties' for the entity
        """
        assert channel is not None or (
            entitykey is not None
        ), "provide at least channel or entitykey (cannot be 'None' together)"
        id = (
            channel
            if entitykey is None
            else entitykey if channel is None else f"{channel}_{entitykey}"
        )
        assert (
            manager.entities.get(id) is None
        ), f"(channel:{channel}, entitykey:{entitykey}) is not unique inside manager.entities"
        self.manager = manager
        self.channel = channel
        self.entitykey = entitykey
        self.namespace_handlers: set[NamespaceHandler] = set()
        self.available = self._attr_available or manager.online
        self.device_class = device_class
        Loggable.__init__(self, id, logger=manager)
        if hasattr(self, "name"):
            name = self.name
        else:
            name = entitykey or device_class
            name = str(name).capitalize() if name else None
        # when channel == 0 it might be the only one so skip it
        # when channel is already in device name it also may be skipped
        if channel and (channel is not manager.id):
            # (channel is manager.id) means this is the 'main' entity of an hub subdevice
            # so we skip adding the subdevice.id to the entity name
            self.name = f"{name} {channel}" if name else str(channel)
        else:
            self.name = name
        self.suggested_object_id = self.name
        self._hass_connected = False
        # by default all of our entities have unique_id so they're registered
        # there could be some exceptions though (MLUpdate)
        self.unique_id = self._generate_unique_id()
        manager.entities[id] = self
        async_add_devices = manager.platforms.setdefault(self.PLATFORM)
        if async_add_devices:
            async_add_devices([self])

    # interface: Entity
    @property
    def device_info(self):
        return self.manager.deviceentry_id

    async def async_added_to_hass(self):
        self.log(self.VERBOSE, "Added to HomeAssistant")
        self._hass_connected = True
        return await super().async_added_to_hass()

    async def async_will_remove_from_hass(self):
        self.log(self.VERBOSE, "Removed from HomeAssistant")
        self._hass_connected = False
        return await super().async_will_remove_from_hass()

    # interface: self
    async def async_shutdown(self):
        for handler in set(self.namespace_handlers):
            handler.unregister(self)
        self.manager.entities.pop(self.id)
        self.manager: EntityManager = None  # type: ignore

    def flush_state(self):
        """Actually commits a state change to HA."""
        if self._hass_connected:
            self.async_write_ha_state()

    def set_available(self):
        self.available = True
        # we don't flush here since we'll wait for actual device readings

    def set_unavailable(self):
        if self.available:
            self.available = False
            self.flush_state()

    def update_native_value(self, native_value):
        """This is a 'debug' friendly definition. It is needed to help static type checking
        when implementing diagnostic sensors calls but, at runtime, it would be an error to
        call such an implementation for an entity which is not a diagnostic sensor."""
        raise NotImplementedError("Called update_native_value on wrong class type")

    def _generate_unique_id(self):
        return self.manager.generate_unique_id(self)

    def _parse(self, payload):
        """Default entity payload message parser. This is invoked automatically
        when the entity is registered to a NamespaceHandler for a given namespace
        and no 'better' _parse_xxxx has been defined. See NamespaceHandler.register.
        At this root level, coming here is likely an error but this feature
        (default parser) is being leveraged to setup a quick parsing route for some
        specific class of entities instead of having to define a specific _parse_xxxx.
        This is useful for generalized sensor classes which are just mapped to a single
        namespace."""
        self.log(
            self.WARNING,
            "Handler undefined for payload:(%s)",
            str(payload),
            timeout=14400,
        )


class MerossNumericEntity(MerossEntity):
    """Common base class for (numeric) sensors and numbers."""

    UNIT_PERCENTAGE: Final = hac.PERCENTAGE

    DEVICECLASS_TO_UNIT_MAP: ClassVar[dict[object | None, str | None]]
    """To be init in derived classes with their DeviceClass own types"""
    device_scale: int | float = 1
    """Used to scale the device value when converting to/from native value"""
    device_value: int | float | None
    """The 'native' device value carried in protocol messages"""

    # HA core entity attributes:
    native_value: int | float | None
    native_unit_of_measurement: str | None

    __slots__ = (
        "device_value",
        "native_value",
        "native_unit_of_measurement",
    )

    def __init__(
        self,
        manager: EntityManager,
        channel: object,
        entitykey: str | None = None,
        device_class: object | None = None,
        *,
        device_value: int | float | None = None,
        native_unit_of_measurement: str | None = None,
    ):
        self.device_value = device_value
        self.native_value = (
            None if device_value is None else device_value / self.device_scale
        )
        self.native_unit_of_measurement = (
            native_unit_of_measurement or self.DEVICECLASS_TO_UNIT_MAP.get(device_class)
        )
        super().__init__(manager, channel, entitykey, device_class)

    def set_unavailable(self):
        self.device_value = None
        self.native_value = None
        super().set_unavailable()

    def update_device_value(self, device_value: int | float):
        if self.device_value != device_value:
            self.device_value = device_value
            self.native_value = device_value / self.device_scale
            self.flush_state()
            return True

    def update_native_value(self, native_value: int | float):
        if self.native_value != native_value:
            self.native_value = native_value
            self.flush_state()
            return True

    def _parse(self, payload: dict):
        """Default parsing for sensor and number entities. Set the proper
        key_value in class/instance definition to make it work."""
        self.update_device_value(payload[self.key_value])


class MerossBinaryEntity(MerossEntity):
    """Partially abstract common base class for ToggleEntity and BinarySensor.
    The initializer is skipped."""

    key_value = mc.KEY_ONOFF

    # HA core entity attributes:
    is_on: bool | None

    __slots__ = ("is_on",)

    def __init__(
        self,
        manager: MerossDeviceBase,
        channel: object,
        entitykey: str | None = None,
        device_class: object | None = None,
        *,
        onoff=None,
    ):
        self.is_on = onoff
        super().__init__(manager, channel, entitykey, device_class)

    def set_unavailable(self):
        self.is_on = None
        super().set_unavailable()

    def update_onoff(self, onoff):
        if self.is_on != onoff:
            self.is_on = onoff
            self.flush_state()

    def _parse(self, payload: dict):
        """Default parsing for toggles and binary sensors. Set the proper
        key_value in class/instance definition to make it work."""
        self.update_onoff(payload[self.key_value])


class MerossToggle(MerossBinaryEntity):
    """
    Base toggle-like behavior used as a base class for
    effective switches or the likes (light for example)
    """

    manager: MerossDeviceBase

    def __init__(
        self,
        manager: MerossDeviceBase,
        channel: object,
        entitykey: str | None = None,
        device_class: object | None = None,
        *,
        onoff=None,
        namespace: str | None = None,
    ):
        super().__init__(
            manager,
            channel,
            entitykey,
            device_class,
            onoff=onoff,
        )
        if namespace:
            self.namespace = namespace
            self.key_namespace = NAMESPACE_TO_KEY[namespace]

    async def async_turn_on(self, **kwargs):
        await self.async_request_onoff(1)

    async def async_turn_off(self, **kwargs):
        await self.async_request_onoff(0)

    async def async_request_onoff(self, onoff: int):
        assert self.namespace

        # this is the meross executor code
        # override for switches not implemented
        # by a toggle like api
        if await self.manager.async_request_ack(
            self.namespace,
            mc.METHOD_SET,
            {
                self.key_namespace: {
                    self.key_channel: self.channel,
                    self.key_value: onoff,
                }
            },
        ):
            self.update_onoff(onoff)


#
# helper functions to 'commonize' platform setup
#
def platform_setup_entry(
    hass: HomeAssistant, config_entry: ConfigEntry, async_add_devices, platform: str
):
    manager = ApiProfile.managers[config_entry.entry_id]
    manager.log(manager.DEBUG, "platform_setup_entry { platform: %s }", platform)
    manager.platforms[platform] = async_add_devices
    async_add_devices(manager.managed_entities(platform))
