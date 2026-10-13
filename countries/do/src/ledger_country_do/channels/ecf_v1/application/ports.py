"""The ports do-ecf-v1's use cases need; adapters implement them (spec §4.5).

``XmlSigner`` becomes fiscal-kit's ``SignerPort`` (spec §8.4) at integration, implemented over the signer
module's gRPC client; in the spike a key held in the test or CLI process implements it.
"""

from abc import ABCMeta, abstractmethod
from typing import Protocol


class XmlSigner(Protocol, metaclass=ABCMeta):
    """Signs DGII's documents with the issuing company's certificate, in DGII's profile."""

    @abstractmethod
    async def sign_document(self, document: bytes) -> bytes:
        """``document`` with DGII's enveloped signature appended as its root's last child."""

    @abstractmethod
    async def sign_seed(self, seed: bytes) -> bytes:
        """DGII's or a receiver's authentication seed (``SemillaModel``), signed the same way."""
