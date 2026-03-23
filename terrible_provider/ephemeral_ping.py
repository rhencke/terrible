"""Hardcoded test ephemeral resource: terrible_ephemeral_ping.

Returns a fixed greeting string. Used to validate the tfplugin6.9 ephemeral
resource protocol handshake end-to-end before wiring up dynamic discovery.
"""

from tf.schema import Schema

from .ephemeral import EphemeralResource

_SCHEMA = Schema(attributes=[])


class TerribleEphemeralPing(EphemeralResource):
    _module_name = ""
    _schema = _SCHEMA
    _name = "terrible_ephemeral_ping"

    @classmethod
    def get_name(cls) -> str:
        return cls._name

    def open(self, diags, config: dict) -> dict:
        return {"greeting": "pong"}

    def close(self, diags, private: bytes):
        pass
