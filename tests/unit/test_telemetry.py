import threading
from enum import Enum

from secrecon.telemetry.runtime import Delivery


def test_blocked_exporter_is_bounded_and_drops_without_blocking_producer():
    entered, release = threading.Event(), threading.Event()

    class Result(Enum):
        SUCCESS = 1

    class Blocked:
        def export(self, items):
            entered.set()
            release.wait(5)
            return Result.SUCCESS

    delivery = Delivery(Blocked(), capacity=4)
    try:
        delivery.offer("first")
        assert entered.wait(2)
        for i in range(1000):
            delivery.offer(i)
        assert delivery.items.qsize() == 4
        assert delivery.dropped == 996
    finally:
        release.set()
        delivery.flush()
        delivery.close()


def test_exporter_exception_is_contained():
    class Broken:
        def export(self, items):
            raise OSError("telemetry backend unavailable")

    delivery = Delivery(Broken())
    try:
        delivery.offer("event")
        assert delivery.flush(2000)
        assert delivery.failed == 1
    finally:
        delivery.close()
