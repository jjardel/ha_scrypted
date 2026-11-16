"""Tests for scrypted event entities."""

from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_send

from custom_components.scrypted.const import SIGNAL_NEW_DEVICE
from tests.conftest import setup_entry


async def test_object_detection_event(hass, fake_sdk, enable_custom_integrations):
    """Object detection event."""
    await setup_entry(hass)

    state = hass.states.get("event.porch_front_door_cam_object_detected")
    assert state is not None
    assert "person" in state.attributes["event_types"]

    fake_sdk.systemManager.fire_device_event(
        "cam1",
        "ObjectDetector",
        {
            "detections": [
                {"className": "person", "score": 0.92, "zones": ["porch"]},
            ],
            "timestamp": 1,
        },
    )
    await hass.async_block_till_done()

    state = hass.states.get("event.porch_front_door_cam_object_detected")
    assert state.attributes["event_type"] == "person"
    assert state.attributes["score"] == 0.92


async def test_unknown_detection_class_extends_event_types(
    hass, fake_sdk, enable_custom_integrations
):
    """Unknown detection class extends event types."""
    await setup_entry(hass)
    fake_sdk.systemManager.fire_device_event(
        "cam1",
        "ObjectDetector",
        {"detections": [{"className": "raccoon", "score": 0.5}], "timestamp": 1},
    )
    await hass.async_block_till_done()
    state = hass.states.get("event.porch_front_door_cam_object_detected")
    assert state.attributes["event_type"] == "raccoon"


async def test_doorbell_press_event(hass, fake_sdk, enable_custom_integrations):
    """Doorbell press event."""
    await setup_entry(hass)

    state = hass.states.get("event.doorbell_doorbell")
    assert state is not None

    fake_sdk.systemManager.set_property("bell1", "binaryState", True)
    await hass.async_block_till_done()
    state = hass.states.get("event.doorbell_doorbell")
    assert state.attributes["event_type"] == "ring"

    # Releasing must not fire another event
    last_changed = state.last_changed
    fake_sdk.systemManager.set_property("bell1", "binaryState", False)
    await hass.async_block_till_done()
    assert hass.states.get("event.doorbell_doorbell").last_changed == last_changed


async def test_object_types_failure_skips_entity(
    hass, fake_sdk, enable_custom_integrations
):
    """Unknown classes -> no entity; motion is filtered so it could never fire."""
    fake_sdk.systemManager.getDeviceById(
        "cam1"
    ).getObjectTypes.side_effect = RuntimeError("nope")
    await setup_entry(hass)
    assert hass.states.get("event.porch_front_door_cam_object_detected") is None


async def test_motion_only_detector_skips_entity(
    hass, fake_sdk, enable_custom_integrations
):
    """Motion-only detectors are covered by the motion binary_sensor."""
    fake_sdk.systemManager.getDeviceById("cam1").getObjectTypes.return_value = {
        "classes": ["motion"]
    }
    await setup_entry(hass)
    assert hass.states.get("event.porch_front_door_cam_object_detected") is None


async def test_motion_class_excluded_from_event_types(
    hass, fake_sdk, enable_custom_integrations
):
    """Motion is excluded and a class listed twice appears once."""
    fake_sdk.systemManager.getDeviceById("cam1").getObjectTypes.return_value = {
        "classes": ["motion", "person", "car", "person"]
    }
    await setup_entry(hass)
    state = hass.states.get("event.porch_front_door_cam_object_detected")
    assert state.attributes["event_types"] == ["person", "car"]


async def test_motion_detections_do_not_fire_events(
    hass, fake_sdk, enable_custom_integrations
):
    """Motion detections do not fire events."""
    await setup_entry(hass)

    # scrypted's motion pipeline reports motion as a detection class; the
    # motion binary_sensor owns that signal, so the event entity ignores it.
    fake_sdk.systemManager.fire_device_event(
        "cam1",
        "ObjectDetector",
        {"detections": [{"className": "motion", "score": 1}], "timestamp": 1},
    )
    await hass.async_block_till_done()
    state = hass.states.get("event.porch_front_door_cam_object_detected")
    assert state.state == "unknown"
    assert "motion" not in state.attributes["event_types"]

    # mixed payloads only fire the classified objects
    fake_sdk.systemManager.fire_device_event(
        "cam1",
        "ObjectDetector",
        {
            "detections": [
                {"className": "motion", "score": 1},
                {"className": "person", "score": 0.9},
            ],
            "timestamp": 2,
        },
    )
    await hass.async_block_till_done()
    state = hass.states.get("event.porch_front_door_cam_object_detected")
    assert state.attributes["event_type"] == "person"
    assert "motion" not in state.attributes["event_types"]


async def test_detection_without_class_ignored(
    hass, fake_sdk, enable_custom_integrations
):
    """Detection without class ignored."""
    await setup_entry(hass)
    fake_sdk.systemManager.fire_device_event(
        "cam1", "ObjectDetector", {"detections": [{"score": 1.0}], "timestamp": 1}
    )
    await hass.async_block_till_done()
    state = hass.states.get("event.porch_front_door_cam_object_detected")
    assert state.state == "unknown"


async def test_new_device_signal_unknown_id_ignored(
    hass, fake_sdk, enable_custom_integrations
):
    """A new-device announcement for an id scrypted cannot resolve adds nothing."""
    entry = await setup_entry(hass)
    before = len(hass.states.async_all("event"))

    async_dispatcher_send(hass, SIGNAL_NEW_DEVICE.format(entry.entry_id), "ghost")
    await hass.async_block_till_done()

    assert len(hass.states.async_all("event")) == before


async def test_multiple_classes_each_fire_an_event(
    hass, fake_sdk, enable_custom_integrations
):
    """Each class in one payload produces its own observable event."""
    await setup_entry(hass)
    entity_id = "event.porch_front_door_cam_object_detected"

    fired: list[str] = []

    @callback
    def _track(event):
        if event.data["entity_id"] != entity_id:
            return
        new_state = event.data.get("new_state")
        if new_state:
            fired.append(new_state.attributes["event_type"])

    hass.bus.async_listen("state_changed", _track)

    fake_sdk.systemManager.fire_device_event(
        "cam1",
        "ObjectDetector",
        {
            "detections": [
                {"className": "person", "score": 0.9},
                {"className": "car", "score": 0.8},
            ],
            "detectionId": "d1",
        },
    )
    await hass.async_block_till_done()

    assert fired == ["person", "car"]
    assert hass.states.get(entity_id).attributes["detection_id"] == "d1"


async def test_detection_geometry_and_tracking_attributes(
    hass, fake_sdk, enable_custom_integrations
):
    """The box, frame size, tracked object id and movement reach the event."""
    await setup_entry(hass)
    fake_sdk.systemManager.fire_device_event(
        "cam1",
        "ObjectDetector",
        {
            "detections": [
                {
                    "className": "vehicle",
                    "score": 0.81,
                    "id": "obj7",
                    "boundingBox": [1200, 300, 640, 360],
                    "movement": {"moving": True, "firstSeen": 1, "lastSeen": 2},
                },
            ],
            "inputDimensions": [3840, 2160],
            "detectionId": "d1",
            "timestamp": 2,
        },
    )
    await hass.async_block_till_done()

    attributes = hass.states.get(
        "event.porch_front_door_cam_object_detected"
    ).attributes
    assert attributes["event_type"] == "vehicle"
    assert attributes["tracking_id"] == "obj7"
    assert attributes["moving"] is True
    assert attributes["bounding_box"] == [1200, 300, 640, 360]
    assert attributes["input_dimensions"] == [3840, 2160]


async def test_untracked_detection_attributes_are_none(
    hass, fake_sdk, enable_custom_integrations
):
    """Detectors without tracking or boxes leave the new attributes empty."""
    await setup_entry(hass)
    fake_sdk.systemManager.fire_device_event(
        "cam1",
        "ObjectDetector",
        {"detections": [{"className": "person", "score": 0.9}], "timestamp": 1},
    )
    await hass.async_block_till_done()

    attributes = hass.states.get(
        "event.porch_front_door_cam_object_detected"
    ).attributes
    assert attributes["tracking_id"] is None
    assert attributes["moving"] is None
    assert attributes["bounding_box"] is None
    assert attributes["input_dimensions"] is None


async def test_moving_object_represents_its_class(
    hass, fake_sdk, enable_custom_integrations
):
    """A parked car must not hide a car driving past in the same payload."""
    await setup_entry(hass)
    fake_sdk.systemManager.fire_device_event(
        "cam1",
        "ObjectDetector",
        {
            "detections": [
                {
                    "className": "vehicle",
                    "score": 0.95,
                    "id": "parked",
                    "movement": {"moving": False, "firstSeen": 1, "lastSeen": 1},
                },
                {
                    "className": "vehicle",
                    "score": 0.7,
                    "id": "passing",
                    "movement": {"moving": True, "firstSeen": 5, "lastSeen": 6},
                },
            ],
            "timestamp": 6,
        },
    )
    await hass.async_block_till_done()

    attributes = hass.states.get(
        "event.porch_front_door_cam_object_detected"
    ).attributes
    assert attributes["tracking_id"] == "passing"
    assert attributes["score"] == 0.7


async def test_highest_score_represents_its_class(
    hass, fake_sdk, enable_custom_integrations
):
    """Without movement data, the most confident detection is reported."""
    await setup_entry(hass)
    fake_sdk.systemManager.fire_device_event(
        "cam1",
        "ObjectDetector",
        {
            "detections": [
                {"className": "person", "score": 0.6, "id": "a"},
                {"className": "person", "id": "b"},
                {"className": "person", "score": 0.9, "id": "c"},
            ],
            "timestamp": 1,
        },
    )
    await hass.async_block_till_done()

    state = hass.states.get("event.porch_front_door_cam_object_detected")
    assert state.attributes["tracking_id"] == "c"
