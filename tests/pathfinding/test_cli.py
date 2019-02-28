import logging
from unittest.mock import DEFAULT, Mock, patch

from click.testing import CliRunner
from eth_utils import is_checksum_address
from web3 import Web3

from pathfinding_service.cli import get_default_registry_and_start_block, main

patch_args = dict(
    target='pathfinding_service.cli',
    PathfindingService=DEFAULT,
    ServiceApi=DEFAULT,
    HTTPProvider=DEFAULT,
    get_default_registry_and_start_block=DEFAULT,
)


def test_bad_eth_client(log, default_cli_args):
    """ Giving a bad `eth-rpc` value should yield a concise error message """
    runner = CliRunner()
    with patch('pathfinding_service.cli.PathfindingService'):
        result = runner.invoke(
            main,
            default_cli_args + ['--eth-rpc', 'http://localhost:12345'],
        )
    assert result.exit_code == 1
    assert log.has(
        'Can not connect to the Ethereum client. Please check that it is running '
        'and that your settings are correct.',
    )


def test_success(default_cli_args):
    """ Calling the pathfinding_service with default args should succeed after heavy mocking """
    runner = CliRunner()
    with patch.multiple(**patch_args) as mocks:
        mocks['get_default_registry_and_start_block'].return_value = Mock(), Mock()
        result = runner.invoke(
            main,
            default_cli_args,
        )
    assert result.exit_code == 0


def test_eth_rpc(default_cli_args):
    """ The `eth-rpc` parameter must reach the `HTTPProvider` """
    runner = CliRunner()
    eth_rpc = 'example.com:1234'
    with patch('pathfinding_service.cli.HTTPProvider') as provider:
        runner.invoke(
            main,
            default_cli_args + ['--eth-rpc', eth_rpc],
        )
        provider.assert_called_with(eth_rpc)


def test_registry_address(default_cli_args):
    """ The `registry_address` parameter must reach the `PathfindingService` """
    runner = CliRunner()
    with patch.multiple(**patch_args) as mocks:
        address = Web3.toChecksumAddress('0x' + '1' * 40)
        result = runner.invoke(
            main,
            default_cli_args + ['--registry-address', address],
        )
        assert result.exit_code == 0
        assert mocks['PathfindingService'].call_args[1]['registry_address'] == address

    # check validation of address format
    def fails_on_registry_check(address):
        result = runner.invoke(main, ['--registry-address', address], catch_exceptions=False)
        assert result.exit_code != 0
        assert 'EIP-55' in result.output

    fails_on_registry_check('1' * 40)  # no 0x
    fails_on_registry_check('0x' + '1' * 41)  # not 40 digits
    fails_on_registry_check('0x' + '1' * 39)  # not 40 digits


def test_start_block(default_cli_args):
    """ The `start_block` parameter must reach the `PathfindingService`

    We also have to pass a registry address, because `start_block` is
    overwritten with a default when no registry has been specified.
    """
    runner = CliRunner()
    with patch.multiple(**patch_args) as mocks:
        mocks['get_default_registry_and_start_block'].return_value = Mock(), Mock()
        start_block = 10
        address = Web3.toChecksumAddress('0x' + '1' * 40)
        result = runner.invoke(
            main,
            default_cli_args + [
                '--registry-address', address, '--start-block', str(start_block)],
        )
        assert result.exit_code == 0
        assert mocks['PathfindingService'].call_args[1]['sync_start_block'] == start_block


def test_confirmations(default_cli_args):
    """ The `confirmations` parameter must reach the `PathfindingService` """
    runner = CliRunner()
    with patch.multiple(**patch_args) as mocks:
        mocks['get_default_registry_and_start_block'].return_value = Mock(), Mock()
        confirmations = 77
        result = runner.invoke(
            main,
            default_cli_args + [
                '--confirmations', str(confirmations)],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert mocks['PathfindingService'].call_args[1]['required_confirmations'] == confirmations


def test_default_registry():
    """ We can fall back to a default registry if none if specified """
    net_version = 3
    contracts_version = '0.3._'
    registry_address, block_number = get_default_registry_and_start_block(
        net_version,
        contracts_version,
    )
    assert is_checksum_address(registry_address)
    assert block_number > 0


def test_shutdown(default_cli_args):
    """ Clean shutdown after KeyboardInterrupt """
    runner = CliRunner()
    with patch.multiple(**patch_args) as mocks:
        mocks['get_default_registry_and_start_block'].return_value = Mock(), Mock()
        mocks['PathfindingService'].return_value.run.side_effect = KeyboardInterrupt
        result = runner.invoke(
            main,
            default_cli_args,
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert 'Exiting' in result.output
        assert mocks['PathfindingService'].return_value.stop.called
        assert mocks['ServiceApi'].return_value.stop.called


def test_log_level(default_cli_args):
    """ Setting of log level via command line switch """
    runner = CliRunner()
    with patch.multiple(**patch_args), patch('logging.basicConfig') as basicConfig:
        for log_level in ('CRITICAL', 'WARNING'):
            runner.invoke(
                main,
                default_cli_args + ['--log-level', log_level],
            )
            # pytest already initializes logging, so basicConfig does not have
            # an effect. Use mocking to check that it's called properly.
            assert logging.getLevelName(
                basicConfig.call_args[1]['level'] == log_level,
            )
