import threading
from typing import TYPE_CHECKING, cast

from saitenka.app.features.subtitle import SubtitleAcquisitionController
from saitenka.app.subtitle_intents import AcquisitionSource
from saitenka.app.subtitle_modes import SubtitleFetchRequest, SubtitleFetchResult
from saitenka.runtime import EffectFinished, EffectId, EffectOutcome, Owner

if TYPE_CHECKING:
    from saitenka.mpvio.ipc import MpvIPC


class Notifications:
    def show(self, *_args, **_kwargs) -> None:
        raise AssertionError("a retired acquisition must not report")


def test_completion_from_a_retired_episode_is_ignored() -> None:
    submitted = []

    def submit(**job) -> bool:
        submitted.append(job)
        return True

    def track_ports():
        raise AssertionError("a retired acquisition must not mutate tracks")

    owner = SubtitleAcquisitionController(
        ipc=cast("MpvIPC", object()),
        stop=threading.Event(),
        get=lambda _name: None,
        notifications=Notifications(),
        track_ports=track_ports,
        submitter=submit,
    )
    request = SubtitleFetchRequest(
        fetch=lambda: (None, "unused"),
        select_if_unchanged=False,
        initial_sid=None,
        replace=False,
        force_select=False,
    )
    owner.submit(request, name="background")

    owner.retire_episode()
    submitted[0]["on_finished"](
        EffectFinished(
            EffectId(1),
            Owner.SUBTITLE,
            submitted[0]["identity"],
            EffectOutcome.SUCCEEDED,
            result=SubtitleFetchResult(
                path=None,
                status="late",
                select_if_unchanged=False,
                initial_sid=None,
            ),
        )
    )


def test_retiring_an_episode_retires_its_retry_configuration() -> None:
    messages = []

    class RecordingNotifications:
        def show(self, text, kind="ok") -> None:
            messages.append((text, kind))

    owner = SubtitleAcquisitionController(
        ipc=cast("MpvIPC", object()),
        stop=threading.Event(),
        get=lambda _name: None,
        notifications=RecordingNotifications(),
        track_ports=lambda: cast("object", None),
        submitter=None,
    )
    owner.configure_retry(lambda _path: lambda: (None, "unused"))

    owner.retire_episode()
    owner.begin("/videos/next.mkv", AcquisitionSource.PROVIDERS)

    assert messages == [("No Japanese subtitle providers enabled", "warn")]
