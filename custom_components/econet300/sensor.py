"""Sensor for Econet300."""

from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .common import Econet300Api, EconetDataCoordinator
from .common_functions import camel_to_snake
from .const import (
    DOMAIN,
    ENTITY_CATEGORY,
    ENTITY_ICON,
    ENTITY_PRECISION,
    ENTITY_SENSOR_DEVICE_CLASS_MAP,
    ENTITY_UNIT_MAP,
    ENTITY_VALUE_PROCESSOR,
    SENSOR_MAP_KEY,
    SENSOR_MIXER_KEY,
    SERVICE_API,
    SERVICE_COORDINATOR,
    STATE_CLASS_MAP,
)
from .entity import EconetEntity, LambdaEntity, MixerEntity

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class EconetSensorEntityDescription(SensorEntityDescription):
    """Describes ecoNET sensor entity."""

    process_val: Callable[[Any], Any] = lambda x: x


class EconetSensor(EconetEntity, SensorEntity):
    """Represents an ecoNET sensor entity."""

    entity_description: EconetSensorEntityDescription

    def __init__(
        self,
        entity_description: EconetSensorEntityDescription,
        coordinator: EconetDataCoordinator,
        api: Econet300Api,
    ):
        """Initialize a new ecoNET sensor entity."""
        self.entity_description = entity_description
        self.api = api
        self._attr_native_value = None
        super().__init__(coordinator)

    def _sync_state(self, value) -> None:
        """Synchronize the state of the sensor entity."""
        self._attr_native_value = self.entity_description.process_val(value)
        self.async_write_ha_state()


class MixerSensor(MixerEntity, EconetSensor):
    """Mixer sensor class."""

    def __init__(
        self,
        description: EconetSensorEntityDescription,
        coordinator: EconetDataCoordinator,
        api: Econet300Api,
        idx: int,
    ):
        """Initialize a new instance of the MixerSensor."""
        super().__init__(description, coordinator, api, idx)


class LambdaSensors(LambdaEntity, EconetSensor):
    """Lambda sensor class."""

    def __init__(
        self,
        description: EconetSensorEntityDescription,
        coordinator: EconetDataCoordinator,
        api: Econet300Api,
    ):
        """Initialize a new instance of the EconetSensor class."""
        super().__init__(description, coordinator, api)


def create_entity_description(
    key: str, process_val=lambda x: x
) -> EconetSensorEntityDescription:
    """Create ecoNET300 sensor entity based on supplied key."""
    _LOGGER.debug("Creating sensor entity description for key: %s", key)
    entity_description = EconetSensorEntityDescription(
        key=key,
        device_class=ENTITY_SENSOR_DEVICE_CLASS_MAP.get(key),
        entity_category=ENTITY_CATEGORY.get(key),
        translation_key=camel_to_snake(key),
        icon=ENTITY_ICON.get(key),
        native_unit_of_measurement=ENTITY_UNIT_MAP.get(
            key,
        ),
        state_class=STATE_CLASS_MAP.get(key, SensorStateClass.MEASUREMENT),
        suggested_display_precision=ENTITY_PRECISION.get(key, 0),
        process_val=ENTITY_VALUE_PROCESSOR.get(key, lambda x: x),
    )
    _LOGGER.debug("Created sensor entity description: %s", entity_description)
    return entity_description


def create_sensors(
    keys: list[str],
    coordinator: EconetDataCoordinator,
    api: Econet300Api,
    entity_class: type,
    process_val=lambda x: x,
    filter_condition: Callable[[str], bool] = lambda key: True,
) -> list:
    """Generic function to create sensors."""
    entities = []
    reg_data = coordinator.data.get("regParams", {})
    sys_data = coordinator.data.get("sysParams", {})

    for key in keys:
        if not filter_condition(key):
            _LOGGER.warning("Key: %s does not meet filter condition", key)
            continue

        # Check both data sources
        value = reg_data.get(key) or sys_data.get(key)
        if value is None:
            _LOGGER.warning(
                "Data for key %s is None in both regParams and sysParams, skipping entity creation.",
                key,
            )
            continue

        entity_desc = create_entity_description(key, process_val)
        entities.append(entity_class(entity_desc, coordinator, api))
        _LOGGER.debug("Created entity for key: %s", key)

    return entities


def create_controller_sensors(
    coordinator: EconetDataCoordinator, api: Econet300Api
) -> list[EconetSensor]:
    """Create controller sensor entities."""
    return create_sensors(
        SENSOR_MAP_KEY["_default"],
        coordinator,
        api,
        EconetSensor,
    )


def can_add_mixer(key: str, coordinator: EconetDataCoordinator) -> bool:
    """Check if a mixer can be added."""
    _LOGGER.debug(
        "Checking if mixer can be added for key: %s, data %s",
        key,
        coordinator.data.get("regParams", {}),
    )
    return (
        coordinator.has_reg_data(key)
        and coordinator.data.get("regParams", {}).get(key) is not None
    )


def create_mixer_sensors(
    coordinator: EconetDataCoordinator, api: Econet300Api
) -> list[MixerSensor]:
    """Create individual sensor descriptions for mixer sensors."""
    entities: list[MixerSensor] = []

    def mixer_filter(key: str) -> bool:
        return all(
            coordinator.data.get("regParams", {}).get(k) is not None
            for k in SENSOR_MIXER_KEY.get(key, [])
        )

    return create_sensors(
        SENSOR_MIXER_KEY.keys(),
        coordinator,
        api,
        MixerSensor,
        filter_condition=mixer_filter,
    )


# Create Lambda sensor entity description and Lambda sensor
def create_lambda_sensors(
    coordinator: EconetDataCoordinator, api: Econet300Api
) -> list[LambdaSensors]:
    """Create controller sensor entities."""
    entities: list[LambdaSensors] = []
    sys_params = coordinator.data.get("sysParams", {})

    # Check if moduleLambdaSoftVer is None
    if coordinator.data.get("sysParams", {}).get("moduleLambdaSoftVer") is None:
        _LOGGER.info("moduleLambdaSoftVer is None, no lambda sensors will be created")
        return []

    return create_sensors(
        SENSOR_MAP_KEY["lambda"],
        coordinator,
        api,
        LambdaSensors,
        process_val=lambda x: x / 10,
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> bool:
    """Set up the sensor platform."""

    def async_gather_entities(
        coordinator: EconetDataCoordinator, api: Econet300Api
    ) -> list[EconetSensor]:
        """Collect all sensor entities."""
        entities = []
        entities.extend(create_controller_sensors(coordinator, api))
        entities.extend(create_mixer_sensors(coordinator, api))
        entities.extend(create_lambda_sensors(coordinator, api))
        return entities

    coordinator = hass.data[DOMAIN][entry.entry_id][SERVICE_COORDINATOR]
    api = hass.data[DOMAIN][entry.entry_id][SERVICE_API]

    entities = async_gather_entities(coordinator, api)
    async_add_entities(entities)
    return True
