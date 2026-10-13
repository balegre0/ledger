"""What JetStream's answers to the chassis's configuration requests mean: never, or not now.

nats-server answers an API request it cannot carry out with an ``err_code`` (``server/errors.json`` of
nats-server v2.12.15). The codes in ``_REFUSALS`` say that the stream or the consumer will never be created or
updated as configured: retrying cannot help, a person must change the configuration. Every other answer means
JetStream cannot serve for now, and the same request may well work later: no responders, every 503
(10008 temporarily unavailable, 10004 incomplete results, 10023 insufficient resources), the other 500s
(10009 no leader, 10007 not assigned, 10118 and 10119 offline, and the generic wrappers 10012, 10049 and
10069, whose text could be anything), the cluster's own 400s (10005 no suitable peers, 10202 membership
changing), 404 10059 (the stream does not exist yet: whoever declares it may create it in a moment) and
400 10058 (a stream of that name was just created with another configuration: by a replica of another
version, starting at the same moment).
"""

from typing import Final

lazy from nats.js.errors import APIError

_REFUSALS: Final = frozenset(
    {
        # The request, or the account it is made for (status 400).
        10002,  # JSAccountResourcesExceededErr
        10003,  # JSBadRequestErr
        10025,  # JSInvalidJSONErr
        10026,  # JSMaximumConsumersLimitErr
        10027,  # JSMaximumStreamsLimitErr
        10133,  # JSReplicasCountCannotBeNegative
        10157,  # JSPedanticErrF
        # A stream's configuration (status 400, and the two 500s that say the configuration is invalid).
        10021,  # JSStreamExternalApiOverlapErrF
        10022,  # JSStreamExternalDelPrefixOverlapsErrF
        10024,  # JSStreamInvalidExternalDeliverySubjErrF
        10030,  # JSMirrorMaxMessageSizeTooBigErr
        10031,  # JSMirrorWithSourcesErr
        10032,  # JSMirrorWithStartSeqAndTimeErr
        10033,  # JSMirrorWithSubjectFiltersErr
        10034,  # JSMirrorWithSubjectsErr
        10046,  # JSSourceMaxMessageSizeTooBigErr
        10052,  # JSStreamInvalidConfigF (500)
        10055,  # JSStreamMirrorNotUpdatableErr
        10061,  # JSStreamReplicasNotUpdatableErr
        10065,  # JSStreamSubjectOverlapErr
        10074,  # JSStreamReplicasNotSupportedErr (500)
        10113,  # JSStreamMaxBytesRequired
        10122,  # JSStreamMaxStreamBytesExceeded
        10123,  # JSStreamMoveAndScaleErr
        10128,  # JSStreamNameContainsPathSeparatorsErr
        10140,  # JSSourceDuplicateDetected
        10141,  # JSSourceInvalidStreamName
        10142,  # JSMirrorInvalidStreamName
        10143,  # JSMirrorWithFirstSeqErr
        10144,  # JSSourceMultipleFiltersNotAllowed
        10145,  # JSSourceInvalidSubjectFilter
        10146,  # JSSourceInvalidTransformDestination
        10147,  # JSSourceOverlappingSubjectFilters
        10150,  # JSMirrorMultipleFiltersNotAllowed
        10151,  # JSMirrorInvalidSubjectFilter
        10152,  # JSMirrorOverlappingSubjectFilters
        10154,  # JSMirrorInvalidTransformDestination
        10155,  # JSStreamTransformInvalidSource
        10156,  # JSStreamTransformInvalidDestination
        10173,  # JSMirrorWithCountersErr
        10186,  # JSMirrorWithMsgSchedulesErr
        10187,  # JSSourceWithMsgSchedulesErr
        10198,  # JSMirrorWithAtomicPublishErr
        # A consumer's configuration: every JSConsumer... error of status 400.
        10013,  # JSConsumerNameExistErr
        10016,  # JSConsumerDurableNameNotInSubjectErr
        10017,  # JSConsumerDurableNameNotMatchSubjectErr
        10018,  # JSConsumerDurableNameNotSetErr
        10019,  # JSConsumerEphemeralWithDurableInSubjectErr
        10020,  # JSConsumerEphemeralWithDurableNameErr
        10078,  # JSConsumerConfigRequiredErr
        10079,  # JSConsumerDeliverToWildcardsErr
        10080,  # JSConsumerPushMaxWaitingErr
        10081,  # JSConsumerDeliverCycleErr
        10082,  # JSConsumerMaxPendingAckPolicyRequiredErr
        10083,  # JSConsumerSmallHeartbeatErr
        10084,  # JSConsumerPullRequiresAckErr
        10085,  # JSConsumerPullNotDurableErr
        10086,  # JSConsumerPullWithRateLimitErr
        10087,  # JSConsumerMaxWaitingNegativeErr
        10088,  # JSConsumerHBRequiresPushErr
        10089,  # JSConsumerFCRequiresPushErr
        10090,  # JSConsumerDirectRequiresPushErr
        10091,  # JSConsumerDirectRequiresEphemeralErr
        10092,  # JSConsumerOnMappedErr
        10093,  # JSConsumerFilterNotSubsetErr
        10094,  # JSConsumerInvalidPolicyErrF
        10095,  # JSConsumerInvalidSamplingErrF
        10098,  # JSConsumerWQRequiresExplicitAckErr
        10099,  # JSConsumerWQMultipleUnfilteredErr
        10100,  # JSConsumerWQConsumerNotUniqueErr
        10101,  # JSConsumerWQConsumerNotDeliverAllErr
        10102,  # JSConsumerNameTooLongErrF
        10103,  # JSConsumerBadDurableNameErr
        10105,  # JSConsumerExistingActiveErr
        10106,  # JSConsumerReplacementWithDifferentNameErr
        10107,  # JSConsumerDescriptionTooLongErrF
        10108,  # JSConsumerWithFlowControlNeedsHeartbeats
        10112,  # JSConsumerInvalidDeliverSubject
        10114,  # JSConsumerMaxRequestBatchNegativeErr
        10115,  # JSConsumerMaxRequestExpiresTooSmall
        10116,  # JSConsumerMaxDeliverBackoffErr
        10121,  # JSConsumerMaxPendingAckExcessErrF
        10125,  # JSConsumerMaxRequestBatchExceededF
        10126,  # JSConsumerReplicasExceedsStream
        10127,  # JSConsumerNameContainsPathSeparatorsErr
        10131,  # JSConsumerCreateFilterSubjectMismatchErr
        10132,  # JSConsumerCreateDurableAndNameMismatch
        10134,  # JSConsumerReplicasShouldMatchStream
        10135,  # JSConsumerMetadataLengthErrF
        10136,  # JSConsumerDuplicateFilterSubjects
        10137,  # JSConsumerMultipleFiltersNotAllowed
        10138,  # JSConsumerOverlappingSubjectFilters
        10139,  # JSConsumerEmptyFilter
        10148,  # JSConsumerAlreadyExists
        10149,  # JSConsumerDoesNotExist
        10153,  # JSConsumerInactiveThresholdExcess
        10159,  # JSConsumerPriorityPolicyWithoutGroup
        10160,  # JSConsumerInvalidPriorityGroupErr
        10161,  # JSConsumerEmptyGroupName
        10162,  # JSConsumerInvalidGroupNameErr
        10178,  # JSConsumerPushWithPriorityGroupErr
        10181,  # JSConsumerAckPolicyInvalidErr
        10182,  # JSConsumerReplayPolicyInvalidErr
        10183,  # JSConsumerAckWaitNegativeErr
        10184,  # JSConsumerBackOffNegativeErr
        10196,  # JSConsumerPriorityGroupWithPolicyNone
        10197,  # JSConsumerPinnedTTLWithoutPriorityPolicyNone
    }
)
"""The ``err_code`` of JetStream's answers to a stream or a consumer it will never accept as configured."""


def is_refusal(answer: APIError) -> bool:
    """Whether ``answer`` says that the configuration will never be accepted, not that it cannot be now."""
    return answer.err_code in _REFUSALS
