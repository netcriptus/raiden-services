import itertools
from datetime import datetime, timezone
from typing import Dict, List

import pytest
from eth_utils import decode_hex

from pathfinding_service.model import ChannelView
from pathfinding_service.model.token_network import TokenNetwork
from raiden.constants import EMPTY_SIGNATURE
from raiden.messages.path_finding_service import PFSFeeUpdate
from raiden.network.transport.matrix.utils import AddressReachability
from raiden.transfer.identifiers import CanonicalIdentifier
from raiden.transfer.mediated_transfer.mediation_fee import FeeScheduleState as RaidenFeeSchedule
from raiden.utils.mediation_fees import ppm_fee_per_channel
from raiden.utils.typing import (
    Address,
    ChainID,
    ChannelID,
    FeeAmount as FA,
    PaymentAmount as PA,
    ProportionalFeeAmount,
    TokenAmount as TA,
    TokenNetworkAddress,
)


class PrettyBytes(bytes):
    def __repr__(self):
        return "a%x" % int.from_bytes(self, byteorder="big")


def a(int_addr) -> Address:  # pylint: disable=invalid-name
    """Create an address from an int with a short representation.

    This is helpful in tests because
    * Address creation is concise
    * You can easily match `a(1)` in your test with `a1` in your test output
    """
    return Address(PrettyBytes([0] * 19 + [int_addr]))


class TokenNetworkForTests(TokenNetwork):
    def __init__(self, channels: List[dict], default_capacity: TA = TA(100)):
        super().__init__(token_network_address=TokenNetworkAddress(a(255)))

        # open channels
        channel_ids = itertools.count(100)
        for chan in channels:
            self.handle_channel_opened_event(
                channel_identifier=ChannelID(next(channel_ids)),
                participant1=a(chan["participant1"]),
                participant2=a(chan["participant2"]),
                settle_timeout=100,
            )

            cv1: ChannelView = self.G[a(chan["participant1"])][a(chan["participant2"])]["view"]
            cv1.capacity = chan.get("capacity1", default_capacity)
            cv2: ChannelView = self.G[a(chan["participant2"])][a(chan["participant1"])]["view"]
            cv2.capacity = chan.get("capacity2", default_capacity)

        # create reachability mapping for testing
        self.address_to_reachability: Dict[Address, AddressReachability] = {
            node: AddressReachability.REACHABLE for node in self.G.nodes
        }

    def set_fee(self, node1: int, node2: int, **fee_params):
        channel_id = self.G[a(node1)][a(node2)]["view"].channel_id
        self.handle_channel_fee_update(
            PFSFeeUpdate(
                canonical_identifier=CanonicalIdentifier(
                    chain_identifier=ChainID(1),
                    token_network_address=self.address,
                    channel_identifier=channel_id,
                ),
                updating_participant=a(node1),
                fee_schedule=RaidenFeeSchedule(**fee_params),
                signature=EMPTY_SIGNATURE,
                timestamp=datetime.now(timezone.utc),
            )
        )

    def estimate_fee(self, initator: int, target: int, value=PA(10), max_paths=1):
        result = self.get_paths(
            source=a(initator),
            target=a(target),
            value=value,
            max_paths=max_paths,
            address_to_reachability=self.address_to_reachability,
        )
        if not result:
            return None
        return result[0]["estimated_fee"]


def test_fees_in_balanced_routing():
    tn = TokenNetworkForTests(
        channels=[dict(participant1=1, participant2=2), dict(participant1=2, participant2=3)]
    )

    # Make sure that routing works and the default fees are zero
    result = tn.get_paths(
        source=a(1),
        target=a(3),
        value=PA(10),
        max_paths=1,
        address_to_reachability=tn.address_to_reachability,
    )
    assert len(result) == 1
    assert [PrettyBytes(decode_hex(node)) for node in result[0]["path"]] == [a(1), a(2), a(3)]
    assert result[0]["estimated_fee"] == 0

    # Fees for the initiator are ignored
    tn.set_fee(1, 2, flat=FA(1))
    assert tn.estimate_fee(1, 3) == 0

    # Node 2 demands fees for incoming transfers
    tn.set_fee(2, 1, flat=FA(1))
    assert tn.estimate_fee(1, 3) == 1

    # Node 2 demands fees for outgoing transfers
    tn.set_fee(2, 3, flat=FA(1))
    assert tn.estimate_fee(1, 3) == 2

    # Same fee in the opposite direction
    assert tn.estimate_fee(3, 1) == 2

    # Reset fees to zero
    tn.set_fee(1, 2)
    tn.set_fee(2, 1)
    tn.set_fee(2, 3)

    # Now let's try imbalance fees
    tn.set_fee(2, 3, imbalance_penalty=[(TA(0), FA(0)), (TA(200), FA(200))])
    assert tn.estimate_fee(1, 3) == 10
    assert tn.estimate_fee(3, 1) == -10

    # The opposite fee schedule should give opposite results
    tn.set_fee(2, 3, imbalance_penalty=[(TA(0), FA(200)), (TA(200), FA(0))])
    assert tn.estimate_fee(1, 3) == -10
    assert tn.estimate_fee(3, 1) == 10

    # When the range covered by the imbalance_penalty does include the
    # necessary balance values, the route should be considered invalid.
    tn.set_fee(2, 3, imbalance_penalty=[(TA(0), FA(0)), (TA(80), FA(200))])
    assert tn.estimate_fee(1, 3) is None


def test_fees_in_unbalanced_routing():
    tn = TokenNetworkForTests(
        channels=[
            dict(participant1=1, participant2=2, capacity1=100, capacity2=0),
            dict(participant1=2, participant2=3, capacity1=100, capacity2=0),
        ]
    )

    # Make sure that routing works and the default fees are zero
    result = tn.get_paths(
        source=a(1),
        target=a(3),
        value=PA(10),
        max_paths=1,
        address_to_reachability=tn.address_to_reachability,
    )
    assert len(result) == 1
    assert [PrettyBytes(decode_hex(node)) for node in result[0]["path"]] == [a(1), a(2), a(3)]
    assert result[0]["estimated_fee"] == 0

    # Fees for the initiator are ignored
    tn.set_fee(1, 2, flat=FA(1))
    assert tn.estimate_fee(1, 3) == 0

    # Node 2 demands fees for incoming transfers
    tn.set_fee(2, 1, flat=FA(1))
    assert tn.estimate_fee(1, 3) == 1

    # Node 2 demands fees for outgoing transfers
    tn.set_fee(2, 3, flat=FA(1))
    assert tn.estimate_fee(1, 3) == 2

    # No capacity in the opposite direction
    assert tn.estimate_fee(3, 1) is None

    # Reset fees to zero
    tn.set_fee(1, 2)
    tn.set_fee(2, 1)
    tn.set_fee(2, 3)

    # Now let's try imbalance fees
    tn.set_fee(2, 3, imbalance_penalty=[(TA(0), FA(0)), (TA(200), FA(200))])
    assert tn.estimate_fee(1, 3) == 10
    assert tn.estimate_fee(3, 1) is None

    # The opposite fee schedule should give opposite results
    tn.set_fee(2, 3, imbalance_penalty=[(TA(0), FA(200)), (TA(200), FA(0))])
    assert tn.estimate_fee(1, 3) == -10
    assert tn.estimate_fee(3, 1) is None

    # When the range covered by the imbalance_penalty does include the
    # necessary balance values, the route should be considered invalid.
    tn.set_fee(2, 3, imbalance_penalty=[(TA(0), FA(0)), (TA(80), FA(200))])
    assert tn.estimate_fee(1, 3) is None


def test_regression():
    tn = TokenNetworkForTests(
        channels=[
            dict(participant1=1, participant2=2, capacity1=100, capacity2=0),
            dict(participant1=2, participant2=3, capacity1=100, capacity2=0),
        ]
    )

    tn.set_fee(2, 1, imbalance_penalty=[(TA(0), FA(200)), (TA(100), FA(0))])
    assert tn.estimate_fee(1, 3) is not None


def test_regression2():
    capacity = TA(100_000)
    tn = TokenNetworkForTests(
        channels=[
            dict(participant1=1, participant2=2, capacity1=capacity, capacity2=0),
            dict(participant1=2, participant2=3, capacity1=capacity, capacity2=0),
        ]
    )

    tn.set_fee(
        2, 1, imbalance_penalty=[(TA(0), FA(1000)), (capacity // 2, 0), (capacity, FA(1000))]
    )
    assert tn.estimate_fee(1, 3, value=10_000) is not None


@pytest.mark.parametrize(
    "flat_fee_cli, prop_fee_cli, estimated_fee",
    [
        # flat fees
        (100, 0, 100 + 100),
        (10, 0, 10 + 10),
        # proportional fees
        # TODO: There are some rounding problems with the 100% case
        (0, 1_000_000, 999 + 1998),  # 100% per hop mediation fee
        (0, 100_000, 100 + 110),  # 10% per hop mediation fee
        (0, 50_000, 50 + 53),  # 5% per hop mediation fee
        (0, 10_000, 10 + 10),  # 1% per hop mediation fee
    ],
)
def test_compounding_fees(flat_fee_cli, prop_fee_cli, estimated_fee):
    """ The transferred amount needs to include the fees for all mediators.
    Earlier mediators will apply the proportional fee not only on the payment
    amount, but also on the fees for later mediators.
    """
    flat_fee = flat_fee_cli // 2
    prop_fee = ppm_fee_per_channel(ProportionalFeeAmount(prop_fee_cli))

    tn = TokenNetworkForTests(
        channels=[
            dict(participant1=1, participant2=2),
            dict(participant1=2, participant2=3),
            dict(participant1=3, participant2=4),
        ],
        default_capacity=TA(10_000),
    )

    tn.set_fee(2, 1, flat=flat_fee, proportional=prop_fee)
    tn.set_fee(2, 3, flat=flat_fee, proportional=prop_fee)
    tn.set_fee(3, 2, flat=flat_fee, proportional=prop_fee)
    tn.set_fee(3, 4, flat=flat_fee, proportional=prop_fee)
    assert tn.estimate_fee(1, 4, value=PA(1_000)) == estimated_fee


@pytest.mark.parametrize(
    "flat_fee, prop_fee_cli, max_lin_imbalance_fee, target_amount, expected_fee",
    [
        # proportional fees
        (0, 1_000_000, 0, 1000, 999),  # 100% per hop mediation fee
        (0, 100_000, 0, 1000, 100),  # 10% per hop mediation fee
        (0, 50_000, 0, 1000, 50),  # 5% per hop mediation fee
        (0, 10_000, 0, 1000, 10),  # 1% per hop mediation fee
        (0, 10_000, 0, 100, 0),  # 1% per hop mediation fee
        (0, 5_000, 0, 101, 0),  # 0,5% per hop mediation fee gets rounded away
        # pure flat fee
        (50, 0, 0, 1000, 100),
        # mixed tests
        (10, 100_000, 0, 1000, 121),
        (100, 500_000, 0, 1000, 750),
        (100, 500_000, 0, 967, 733),
        # imbalance fee
        (0, 0, 100, 1_000, 10),
        (0, 0, 1_000, 1_000, 100),
    ],
)
def test_fee_estimate(flat_fee, prop_fee_cli, max_lin_imbalance_fee, target_amount, expected_fee):
    """ Tests the backwards fee calculation. """
    capacity = TA(10_000)

    prop_fee = ppm_fee_per_channel(ProportionalFeeAmount(prop_fee_cli))
    imbalance_fee = None
    if max_lin_imbalance_fee > 0:
        # This created a simple unsymmetric imbalance fee
        imbalance_fee = [(0, 0), (capacity, 0), (2 * capacity, max_lin_imbalance_fee)]

    tn = TokenNetworkForTests(
        channels=[dict(participant1=1, participant2=2), dict(participant1=2, participant2=3)],
        default_capacity=capacity,
    )

    tn.set_fee(2, 1, flat=flat_fee, proportional=prop_fee, imbalance_penalty=imbalance_fee)
    tn.set_fee(2, 3, flat=flat_fee, proportional=prop_fee, imbalance_penalty=imbalance_fee)
    assert tn.estimate_fee(1, 3, value=PA(target_amount)) == expected_fee
