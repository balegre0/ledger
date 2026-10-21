"""The signer's contract as generated: ``ledger.signer.v1.SignerService`` and its five methods.

The callers' allowlists (spec §3.7, the plan's Decision 12) name the methods by their full names; these are
the names the generated descriptor holds.
"""

from typing import Final

from ledger_signer.adapters.grpc._generated.v1 import signer_pb2, signer_pb2_grpc
from ledger_signer.adapters.grpc.server import CALLER_METHODS

SERVICE: Final = "/ledger.signer.v1.SignerService/"
IMPORT: Final = SERVICE + "ImportCertificate"
DESCRIBE: Final = SERVICE + "DescribeCertificates"
RETIRE: Final = SERVICE + "RetireCertificate"
SIGN_XML: Final = SERVICE + "SignXml"
SIGN_SEED: Final = SERVICE + "SignSeed"
ALLOWLISTS: Final = {
    "api": frozenset({IMPORT, DESCRIBE, RETIRE}),
    "worker": frozenset({SIGN_XML, SIGN_SEED, DESCRIBE}),
    "receiver": frozenset({SIGN_XML, SIGN_SEED, DESCRIBE}),
}


def test_the_generated_stubs_import_and_name_the_service() -> None:
    service = signer_pb2.DESCRIPTOR.services_by_name["SignerService"]
    assert (signer_pb2.DESCRIPTOR.package, service.full_name) == (
        "ledger.signer.v1",
        "ledger.signer.v1.SignerService",
    )
    assert callable(signer_pb2_grpc.SignerServiceStub)
    assert callable(signer_pb2_grpc.add_SignerServiceServicer_to_server)


def test_the_service_s_five_methods_by_full_name_are_those_the_allowlists_name() -> None:
    service = signer_pb2.DESCRIPTOR.services_by_name["SignerService"]
    methods = frozenset(f"/{service.full_name}/{method.name}" for method in service.methods)
    assert methods == {IMPORT, DESCRIBE, RETIRE, SIGN_XML, SIGN_SEED}
    assert frozenset().union(*ALLOWLISTS.values()) == methods


def test_the_server_s_allowlists_are_these() -> None:
    assert CALLER_METHODS == ALLOWLISTS


def test_only_the_import_streams_its_requests() -> None:
    service = signer_pb2.DESCRIPTOR.services_by_name["SignerService"]
    streaming = {
        method.name: (method.client_streaming, method.server_streaming) for method in service.methods
    }
    assert streaming == {
        "ImportCertificate": (True, False),
        "DescribeCertificates": (False, False),
        "RetireCertificate": (False, False),
        "SignXml": (False, False),
        "SignSeed": (False, False),
    }


def test_unspecified_is_every_enumeration_s_zero() -> None:
    zeros = {
        name: enum.values_by_number[0].name for name, enum in signer_pb2.DESCRIPTOR.enum_types_by_name.items()
    }
    assert zeros == {
        "Mode": "MODE_UNSPECIFIED",
        "Backend": "BACKEND_UNSPECIFIED",
        "CertificateStatus": "CERTIFICATE_STATUS_UNSPECIFIED",
        "SignatureProfile": "SIGNATURE_PROFILE_UNSPECIFIED",
    }
