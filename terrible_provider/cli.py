import sys

from tf.provider import ProviderServicer

from .ephemeral import EphemeralMixin
from .provider import TerribleProvider


class _TerribleServicer(EphemeralMixin, ProviderServicer):
    """ProviderServicer extended with ephemeral resource support (tfplugin6.9)."""


def _add_to_server(servicer, server):
    """Register the provider servicer on *server*.

    Uses tf's own grpc stubs for all pre-existing RPCs so that request objects
    arrive as tf.gen proto types (required by tf.provider's isinstance checks).
    Adds the new tfplugin6.9 RPCs (ephemeral + identity) on top using our gen
    proto types, then overrides GetProviderSchema's response serialiser to one
    that can encode ephemeral_resource_schemas.
    """
    from typing import Any, cast

    import grpc
    import tf.gen.tfplugin_pb2 as tf_pb
    import tf.gen.tfplugin_pb2_grpc as tf_grpc

    from .gen import tfplugin_pb2 as _epb

    epb = cast(Any, _epb)

    # Register all existing RPCs with tf's proto types (step 1: tf's own handler).
    tf_grpc.add_ProviderServicer_to_server(servicer, server)

    # Override GetProviderSchema to use our 6.9 response serialiser so that
    # ephemeral_resource_schemas is preserved when we return a 6.9 Response.
    schema_handler = grpc.unary_unary_rpc_method_handler(
        servicer.GetProviderSchema,
        request_deserializer=tf_pb.GetProviderSchema.Request.FromString,
        response_serializer=epb.GetProviderSchema.Response.SerializeToString,
    )

    # New 6.9-only RPCs (not present in tf's stubs).
    new_handlers = {
        "GetProviderSchema": schema_handler,
        "GetResourceIdentitySchemas": grpc.unary_unary_rpc_method_handler(
            servicer.GetResourceIdentitySchemas,
            request_deserializer=epb.GetResourceIdentitySchemas.Request.FromString,
            response_serializer=epb.GetResourceIdentitySchemas.Response.SerializeToString,
        ),
        "UpgradeResourceIdentity": grpc.unary_unary_rpc_method_handler(
            servicer.UpgradeResourceIdentity,
            request_deserializer=epb.UpgradeResourceIdentity.Request.FromString,
            response_serializer=epb.UpgradeResourceIdentity.Response.SerializeToString,
        ),
        "ValidateEphemeralResourceConfig": grpc.unary_unary_rpc_method_handler(
            servicer.ValidateEphemeralResourceConfig,
            request_deserializer=epb.ValidateEphemeralResourceConfig.Request.FromString,
            response_serializer=epb.ValidateEphemeralResourceConfig.Response.SerializeToString,
        ),
        "OpenEphemeralResource": grpc.unary_unary_rpc_method_handler(
            servicer.OpenEphemeralResource,
            request_deserializer=epb.OpenEphemeralResource.Request.FromString,
            response_serializer=epb.OpenEphemeralResource.Response.SerializeToString,
        ),
        "RenewEphemeralResource": grpc.unary_unary_rpc_method_handler(
            servicer.RenewEphemeralResource,
            request_deserializer=epb.RenewEphemeralResource.Request.FromString,
            response_serializer=epb.RenewEphemeralResource.Response.SerializeToString,
        ),
        "CloseEphemeralResource": grpc.unary_unary_rpc_method_handler(
            servicer.CloseEphemeralResource,
            request_deserializer=epb.CloseEphemeralResource.Request.FromString,
            response_serializer=epb.CloseEphemeralResource.Response.SerializeToString,
        ),
    }
    server.add_registered_method_handlers("tfplugin6.Provider", new_handlers)


def main(argv=None):
    import json
    import os
    import tempfile
    from concurrent import futures

    import grpc
    from tf.gen import grpc_controller_pb2 as controller_pb
    from tf.gen import grpc_controller_pb2_grpc as controller_rpc
    from tf.gen import grpc_stdio_pb2_grpc as stdio_rpc
    from tf.runner import _LoggingInterceptor, _self_signed_cert, _ShutdownInterceptor

    argv = argv or sys.argv
    p = TerribleProvider()
    servicer = _TerribleServicer(p)

    stopper = _ShutdownInterceptor()
    server = grpc.server(
        thread_pool=futures.ThreadPoolExecutor(max_workers=10),
        interceptors=[_LoggingInterceptor(), stopper],
    )
    stopper.server = server

    # Register provider RPCs: tf's stubs for existing ones, 6.9 stubs for new ones.
    _add_to_server(servicer, server)

    class GRPCControllerServicer(controller_rpc.GRPCControllerServicer):
        def Shutdown(self, request, context):
            stopper.stopped = True
            return controller_pb.Empty()

    controller_rpc.add_GRPCControllerServicer_to_server(GRPCControllerServicer(), server)

    class GRPCStdioServicer(stdio_rpc.GRPCStdioServicer):
        def StreamStdio(self, request, context):
            return iter([])

    stdio_rpc.add_GRPCStdioServicer_to_server(GRPCStdioServicer(), server)

    with tempfile.TemporaryDirectory() as tmp:
        sock_file = f"{tmp}/py-tf-plugin.sock" if "--stable" not in argv else "/tmp/py-tf-plugin.sock"
        tx = f"unix://{sock_file}"

        if "--dev" in argv:
            print("Running in dev mode\n")
            server.add_insecure_port(tx)
            conf = json.dumps(
                {
                    p.full_name(): {
                        "Protocol": "grpc",
                        "ProtocolVersion": 6,
                        "Pid": os.getpid(),
                        "Test": True,
                        "Addr": {"Network": "unix", "String": sock_file},
                    },
                }
            )
            print(f"\texport TF_REATTACH_PROVIDERS='{conf}'")
            server.start()
            server.wait_for_termination()
            return

        import base64

        server_chain, server_ssl_config = _self_signed_cert()
        server.add_secure_port(tx, server_ssl_config)
        server.start()

        print(
            "|".join(["1", "6", "unix", sock_file, "grpc", base64.b64encode(server_chain).decode().rstrip("=")]) + "\n",
            flush=True,
        )

        try:
            while server.wait_for_termination(0.05):
                if stopper.stopped:
                    break
        except KeyboardInterrupt:
            server.stop(grace=0.5)


if __name__ == "__main__":
    main()
