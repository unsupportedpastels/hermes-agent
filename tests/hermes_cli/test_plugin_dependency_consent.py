"""Only declared Python dependencies require Python install consent."""
import json
import os
import subprocess

import pytest
import hermes_yaml as yaml

from hermes_cli import plugins_cmd
from tests.pm.test_plugin_survival_contract import admission_env


@pytest.mark.parametrize('python_surface', [None, 'pyproject', 'legacy'])
def test_install_only_requests_relevant_python_consent(admission_env, monkeypatch, capsys, python_surface):
    root, home = admission_env
    source = root / 'plugin-source'
    source.mkdir()
    manifest = {'name': 'sidecar-plugin'}
    if python_surface == 'legacy':
        manifest['python_dependencies'] = ['never-install-without-consent==1']
    (source / 'plugin.yaml').write_text(yaml.safe_dump(manifest), encoding='utf-8')
    (source / '__init__.py').write_text('def register(ctx):\n    pass\n', encoding='utf-8')
    (source / 'package.json').write_text(json.dumps({'name': 'sidecar-plugin', 'version': '1.0.0'}), encoding='utf-8')
    if python_surface == 'pyproject':
        (source / 'pyproject.toml').write_text(
            '[project]\nname="sidecar-plugin"\nversion="1.0"\nrequires-python=">=3.14"\n'
            'dependencies=["never-install-without-consent==1"]\n', encoding='utf-8')
    env = {k: v for k, v in os.environ.items() if k not in {'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE'}}
    for args in [('init', '-q'), ('add', '.'), ('-c', 'user.name=test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'fixture')]:
        subprocess.run(['git', *args], cwd=source, env=env, check=True, capture_output=True, timeout=30)
    monkeypatch.setattr(plugins_cmd.sys.stdin, 'isatty', lambda: False)
    config_path = home / 'config.yaml'
    before = config_path.read_bytes()
    plugins_cmd.cmd_install(source.as_uri(), enable=True, allow_removed=True)
    installed = home / 'plugins' / 'sidecar-plugin'
    assert (installed / 'package.json').is_file()
    assert not (installed / 'node_modules').exists()
    enabled = yaml.safe_load(config_path.read_text(encoding='utf-8'))['plugins']['enabled']
    output = capsys.readouterr().out
    if python_surface is None:
        assert 'sidecar-plugin' in enabled
        assert 'declares Python dependencies' not in output
    else:
        assert 'sidecar-plugin' not in enabled
        assert config_path.read_bytes() == before
        assert 'dependency install skipped (non-interactive)' in output


def test_empty_python_dependencies_install_and_enable_without_consent(admission_env, monkeypatch, capsys):  # health: allow F811 -- pytest injects the imported admission_env fixture by parameter name
    """``dependencies = []`` installs nothing, so a headless ``install --enable`` enables the plugin
    exactly like one without a pyproject instead of refusing at an empty consent question."""
    root, home = admission_env
    source = root / 'plugin-source'
    source.mkdir()
    (source / 'plugin.yaml').write_text(yaml.safe_dump({'name': 'no-deps-plugin'}), encoding='utf-8')
    (source / '__init__.py').write_text('def register(ctx):\n    pass\n', encoding='utf-8')
    (source / 'pyproject.toml').write_text(
        '[project]\nname="no-deps-plugin"\nversion="1.0"\nrequires-python=">=3.11"\ndependencies=[]\n',
        encoding='utf-8')
    env = {k: v for k, v in os.environ.items() if k not in {'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE'}}
    for args in [('init', '-q'), ('add', '.'), ('-c', 'user.name=test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'fixture')]:
        subprocess.run(['git', *args], cwd=source, env=env, check=True, capture_output=True, timeout=30)
    monkeypatch.setattr(plugins_cmd.sys.stdin, 'isatty', lambda: False)

    plugins_cmd.cmd_install(source.as_uri(), enable=True, allow_removed=True)

    enabled = yaml.safe_load((home / 'config.yaml').read_text(encoding='utf-8'))['plugins']['enabled']
    output = capsys.readouterr().out
    assert 'no-deps-plugin' in enabled, 'install completed but the plugin was not enabled; CLI output:\n' + output
    assert 'declares Python dependencies' not in output


def test_node_sidecar_question_stays_independent(tmp_path, monkeypatch):
    from pm import workspace

    target = tmp_path / 'plugin'
    target.mkdir()
    (target / 'package.json').write_text('{}', encoding='utf-8')
    prompts, installs, lines = [], [], []
    monkeypatch.setattr(plugins_cmd.sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(plugins_cmd.sys.stdout, 'isatty', lambda: True)
    monkeypatch.setattr('builtins.input', lambda prompt: prompts.append(prompt) or 'yes')
    monkeypatch.setattr(workspace, 'install_node_sidecar', lambda path, **kwargs: installs.append((path, kwargs)))
    from types import SimpleNamespace

    result = plugins_cmd._install_plugin_python_deps(
        {'name': 'sidecar'}, target, SimpleNamespace(print=lambda *args, **kwargs: lines.extend(args)))
    assert result == (True, None)
    assert installs == [(target, {'explicit': True})]
    assert len(prompts) == 1 and 'node_modules' in prompts[0]
    assert not any('Python dependencies' in str(line) for line in lines)


_TOKENS = (('<<', None), ('declares Node', 'declares'), ('Skipped Node', 'skipped'), ('Node deps: npm', 'warn'),
           ('Replacement installed', 'replacement'), ('node-plugin enabled', 'enabled'), ('not enabled', 'not-enabled'),
           ('Cannot enable', 'refused'))
_NPM_OK, _NPM_FAILS = None, 'npm ci exited 1: boom'
# Every install path for a plugin shipping package.json; main runs npm in the live tree after publication.
# The one path that runs it in the staged tree is a TTY --enable fresh install with nothing else to ask first.
_MATRIX = {
    'fresh tty yes': ('fresh', True, {}, 'env? enable? declares node? npm:live warn enabled'),
    'fresh tty node no': ('fresh', True, {}, 'env? enable? declares node? skipped enabled'),
    'fresh tty decline enable': ('fresh', True, {}, 'env? enable? not-enabled'),
    'fresh non-tty': ('fresh', False, {}, 'env? not-enabled'),
    'fresh non-tty --enable': ('fresh', False, {'enable': True}, 'env? declares skipped enabled'),
    'fresh tty --enable': ('fresh-env-set', True, {'enable': True}, 'declares node? npm:staged warn enabled'),
    'fresh tty --enable --yes-deps': ('fresh-env-set', True, {'enable': True, 'yes_deps': True},
                                      'declares node? npm:staged warn enabled'),
    'fresh tty --enable env prompt': ('fresh', True, {'enable': True}, 'env? declares node? npm:live warn enabled'),
    'fresh --no-deps': ('fresh', True, {'no_deps': True}, 'env? not-enabled'),
    'fresh --no-enable': ('fresh', True, {'enable': False}, 'env? not-enabled'),
    'fresh --yes-deps': ('fresh', True, {'yes_deps': True}, 'env? enable? declares node? npm:live warn enabled'),
    'active tty': ('active', True, {}, 'declares node? npm:staged warn replacement'),
    'active --yes-deps': ('active', True, {'yes_deps': True}, 'replacement'),
    'active --yes-deps --enable': ('active', True, {'yes_deps': True, 'enable': True}, 'replacement'),
    'active --no-enable': ('active', True, {'enable': False}, 'declares node? npm:staged warn replacement'),
}


@pytest.mark.parametrize('npm_error', [_NPM_OK, _NPM_FAILS], ids=['npm-ok', 'npm-fails'])
@pytest.mark.parametrize('case', list(_MATRIX))
def test_install_paths_match_main_for_a_node_plugin(admission_env, monkeypatch, capsys, case, npm_error):  # health: allow F811 -- pytest injects the imported admission_env fixture by parameter name
    """The prompts, npm runs, warnings and final state of every CLI install path. An npm failure only
    warns (fail open); the Node question is never moved ahead of the env, Enable or consent prompts."""
    import re

    from hermes_cli import plugins_cmd_install
    from pm import workspace

    setup, tty, flags, expected = _MATRIX[case]
    root, home = admission_env
    source = root / 'plugin-source'
    source.mkdir()
    (source / 'plugin.yaml').write_text(yaml.safe_dump({'name': 'node-plugin', 'requires_env': ['NODE_PLUGIN_TOKEN']}), encoding='utf-8')
    (source / '__init__.py').write_text('def register(ctx):\n    pass\n', encoding='utf-8')
    (source / 'package.json').write_text(json.dumps({'name': 'node-plugin', 'version': '1.0.0'}), encoding='utf-8')
    env = {k: v for k, v in os.environ.items() if k not in {'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE'}}
    for args in [('init', '-q'), ('add', '.'), ('-c', 'user.name=test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'fixture')]:
        subprocess.run(['git', *args], cwd=source, env=env, check=True, capture_output=True, timeout=30)
    monkeypatch.setenv('NODE_PLUGIN_TOKEN', 'set')
    if setup == 'fresh':
        monkeypatch.delenv('NODE_PLUGIN_TOKEN')
    installed = home / 'plugins' / 'node-plugin'
    monkeypatch.setattr(workspace, 'install_node_sidecar', lambda path, **_: (
        print(f"<<npm:{'live' if path == installed else 'staged'}>>"), (path / 'node_modules').mkdir(), npm_error)[2])
    if setup == 'active':
        monkeypatch.setattr(plugins_cmd.sys.stdin, 'isatty', lambda: False)
        plugins_cmd.cmd_install(source.as_uri(), enable=True, allow_removed=True)
        flags = {**flags, 'force': True}
    capsys.readouterr()
    answer_no = {'fresh tty node no': 'node', 'fresh tty decline enable': 'enable'}.get(case)

    def ask(kind, value):
        print(f'<<{kind}?>>')
        return value if kind != answer_no else 'n'
    monkeypatch.setattr(plugins_cmd.sys.stdin, 'isatty', lambda: tty)
    monkeypatch.setattr(plugins_cmd.sys.stdout, 'isatty', lambda: tty)
    monkeypatch.setattr(plugins_cmd, '_ask_yes', lambda prompt: ask('enable', 'y') == 'y')
    monkeypatch.setattr(plugins_cmd_install, 'line_input', lambda prompt: ask('env', 'value'))
    monkeypatch.setattr('builtins.input', lambda prompt: ask('node' if 'node_modules' in prompt else 'other', 'y'))

    plugins_cmd.cmd_install(source.as_uri(), allow_removed=True, **flags)

    lines = re.sub(r'\x1b\[[0-9;]*m', '', capsys.readouterr().out).splitlines()
    got = [m.group(1) if tag is None else tag for line in lines for key, tag in _TOKENS
           if key in line for m in [re.search(r'<<(.*?)>>', line)] if tag is not None or m]
    if npm_error is _NPM_OK:
        expected = expected.replace(' warn', '')
    assert ' '.join(got) == expected
    assert (installed / 'node_modules').is_dir() is ('npm:' in expected)
    enabled = yaml.safe_load((home / 'config.yaml').read_text(encoding='utf-8'))['plugins']['enabled']
    assert ('node-plugin' in enabled) is (setup == 'active' or expected.endswith(' enabled'))


def test_cli_install_without_package_json_runs_no_node_step(admission_env, monkeypatch):  # health: allow F811 -- pytest injects the imported admission_env fixture by parameter name
    from pm import workspace

    root, home = admission_env
    source = root / 'plugin-source'
    source.mkdir()
    (source / 'plugin.yaml').write_text(yaml.safe_dump({'name': 'node-plugin'}), encoding='utf-8')
    (source / '__init__.py').write_text('def register(ctx):\n    pass\n', encoding='utf-8')
    env = {k: v for k, v in os.environ.items() if k not in {'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE'}}
    for args in [('init', '-q'), ('add', '.'), ('-c', 'user.name=test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'fixture')]:
        subprocess.run(['git', *args], cwd=source, env=env, check=True, capture_output=True, timeout=30)
    calls = []
    monkeypatch.setattr(workspace, 'install_node_sidecar', lambda path, **_: calls.append(path))
    monkeypatch.setattr(plugins_cmd.sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(plugins_cmd.sys.stdout, 'isatty', lambda: True)
    monkeypatch.setattr('builtins.input', lambda _prompt: 'y')

    plugins_cmd.cmd_install(source.as_uri(), enable=True, allow_removed=True)

    assert calls == []
    assert (home / 'plugins' / 'node-plugin' / 'plugin.yaml').is_file()


def test_yes_deps_carries_consent_through_non_interactive_install(admission_env, monkeypatch, capsys):
    """--yes-deps (#122134): the flag is the user's own answer to the consent
    question, so a non-interactive install publishes AND enables in one run
    instead of being refused (the `enable` recovery cannot publish a plugin)."""
    root, home = admission_env
    source = root / 'plugin-source'
    source.mkdir()
    (source / 'plugin.yaml').write_text(yaml.safe_dump({'name': 'yes-deps-plugin'}), encoding='utf-8')
    (source / '__init__.py').write_text('def register(ctx):\n    pass\n', encoding='utf-8')
    (source / 'pyproject.toml').write_text(
        '[project]\nname="yes-deps-plugin"\nversion="1.0"\nrequires-python=">=3.11"\n', encoding='utf-8')
    env = {k: v for k, v in os.environ.items() if k not in {'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE'}}
    for args in [('init', '-q'), ('add', '.'), ('-c', 'user.name=test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'fixture')]:
        subprocess.run(['git', *args], cwd=source, env=env, check=True, capture_output=True, timeout=30)
    monkeypatch.setattr(plugins_cmd.sys.stdin, 'isatty', lambda: False)

    plugins_cmd.cmd_install(source.as_uri(), enable=True, allow_removed=True, yes_deps=True)

    enabled = yaml.safe_load((home / 'config.yaml').read_text(encoding='utf-8'))['plugins']['enabled']
    output = capsys.readouterr().out
    # The install publishes into plugins/ and THEN a second PM admission transaction
    # enables it; that admission catches every exception as a refusal, so an
    # environment-sensitive failure there leaves the plugin installed-but-disabled
    # with exit 0. Carry the CLI output in the failure message so the refusal's
    # cause is visible on any machine instead of a bare `in []`.
    assert 'yes-deps-plugin' in enabled, (
        'install completed but the plugin was not enabled; CLI output:\n' + output)
    assert 'dependency install skipped (non-interactive)' not in output


def test_dashboard_install_publishes_a_plugin_the_config_already_selects(admission_env, monkeypatch):  # health: allow F811 -- pytest injects the imported admission_env fixture by parameter name
    """The Desktop/dashboard Install click on a plugin memory.provider already names (a provider that
    left core, a synced config, a re-install after remove) publishes like any fresh install instead of
    the non-interactive 'Install declined' that a retry can never get past. A forced replacement of an
    installed plugin keeps the veto: that is the reinstall the click did not review."""
    root, home = admission_env
    source = root / 'mem-source'
    source.mkdir()
    (source / 'plugin.yaml').write_text(yaml.safe_dump({'name': 'mem-twin'}), encoding='utf-8')
    (source / '__init__.py').write_text('class Twin(MemoryProvider): ...\n', encoding='utf-8')
    (source / 'pyproject.toml').write_text(
        '[project]\nname="mem-twin"\nversion="1.0"\nrequires-python=">=3.11"\n', encoding='utf-8')
    env = {k: v for k, v in os.environ.items() if k not in {'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE'}}
    for args in [('init', '-q'), ('add', '.'), ('-c', 'user.name=test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'fixture')]:
        subprocess.run(['git', *args], cwd=source, env=env, check=True, capture_output=True, timeout=30)
    config = yaml.safe_load((home / 'config.yaml').read_text(encoding='utf-8'))
    config['memory'] = {'provider': 'mem-twin'}
    (home / 'config.yaml').write_text(yaml.safe_dump(config), encoding='utf-8')
    monkeypatch.setattr(plugins_cmd.sys.stdin, 'isatty', lambda: False)

    result = plugins_cmd.dashboard_install_plugin(source.as_uri(), force=False, enable=True)
    assert result['ok'], result
    assert (home / 'plugins' / 'mem-twin' / 'plugin.yaml').is_file()
    # The forced replacement now declares a dependency: installing it is what the veto guards.
    (source / 'pyproject.toml').write_text(
        '[project]\nname="mem-twin"\nversion="1.1"\nrequires-python=">=3.11"\n'
        'dependencies=["never-install-without-consent==1"]\n', encoding='utf-8')
    subprocess.run(['git', '-c', 'user.name=test', '-c', 'user.email=test@example.invalid', 'commit', '-qam', 'deps'],
                   cwd=source, env=env, check=True, capture_output=True, timeout=30)
    again = plugins_cmd.dashboard_install_plugin(source.as_uri(), force=True, enable=True)
    assert not again['ok'] and 'Reinstall declined' in again['error']


def test_publish_assume_consent_skips_replacement_veto(tmp_path, monkeypatch):
    """The active-replacement veto at publication honors --yes-deps: with the
    explicit answer the PM handoff runs; the default without it stays the
    non-interactive refusal (#122134's 'Reinstall declined' half)."""
    from hermes_cli import plugins_transaction
    from pm import workspace as ws

    staged, target = tmp_path / 'staged', tmp_path / 'target'
    staged.mkdir()
    target.mkdir()
    (staged / 'plugin.yaml').write_text(yaml.safe_dump({'name': 'active-plugin'}), encoding='utf-8')
    (staged / 'pyproject.toml').write_text(
        '[project]\nname="active-plugin"\nversion="1.0"\nrequires-python=">=3.11"\n'
        'dependencies=["never-install-without-consent==1"]\n', encoding='utf-8')
    monkeypatch.setattr(ws, 'enabled_plugin_dirs', lambda **kwargs: [target])
    syncs = []
    monkeypatch.setattr('pm.client.sync_venv', lambda **kwargs: syncs.append(kwargs))
    monkeypatch.setattr(plugins_cmd.sys.stdin, 'isatty', lambda: False)

    plugins_transaction.publish_plugin(
        staged, target, {}, {'active-plugin': {}}, require_consent=True, assume_consent=True)
    assert len(syncs) == 1

    with pytest.raises(plugins_cmd.PluginOperationError, match='Reinstall declined'):
        plugins_transaction.publish_plugin(staged, target, {}, {'active-plugin': {}}, require_consent=True)


def test_install_parser_offers_yes_deps_exclusive_with_no_deps():
    import argparse

    from hermes_cli.subcommands.plugins import build_plugins_parser

    parser = argparse.ArgumentParser(prog='hermes')
    build_plugins_parser(parser.add_subparsers(), cmd_plugins=lambda args: None)
    ns = parser.parse_args(['plugins', 'install', 'x', '--yes-deps'])
    assert ns.yes_deps is True
    assert ns.no_deps is False
    with pytest.raises(SystemExit):
        parser.parse_args(['plugins', 'install', 'x', '--no-deps', '--yes-deps'])
