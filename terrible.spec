# PyInstaller spec for terraform-provider-terrible
# Bundles CPython + Ansible + all deps into a single native executable.
#
# Build:
#   pyinstaller terrible.spec
# or via make:
#   make build-binary
#
# Output: dist/terraform-provider-terrible

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

# Collect entire packages that use dynamic loading
ansible_datas, ansible_binaries, ansible_hiddenimports = collect_all('ansible')
tf_datas, tf_binaries, tf_hiddenimports = collect_all('tf')
grpc_datas, grpc_binaries, grpc_hiddenimports = collect_all('grpc')
grpc_tools_datas, grpc_tools_binaries, grpc_tools_hiddenimports = collect_all('grpc._channel')

# Jinja2 extensions loaded by Ansible at runtime
jinja2_datas, jinja2_binaries, jinja2_hiddenimports = collect_all('jinja2')

# cryptography (used by Ansible Vault)
crypto_datas, crypto_binaries, crypto_hiddenimports = collect_all('cryptography')

a = Analysis(
    ['terrible_provider/cli.py'],
    pathex=['.'],
    binaries=ansible_binaries + tf_binaries + grpc_binaries + crypto_binaries,
    datas=(
        ansible_datas
        + tf_datas
        + grpc_datas
        + jinja2_datas
        + crypto_datas
        + collect_data_files('ansible_collections', include_py_files=True)
    ),
    hiddenimports=(
        ansible_hiddenimports
        + tf_hiddenimports
        + grpc_hiddenimports
        + jinja2_hiddenimports
        + crypto_hiddenimports
        + collect_submodules('ansible')
        + collect_submodules('ansible_collections')
        + [
            # Ansible internals loaded via string at runtime
            'ansible.executor.task_queue_manager',
            'ansible.plugins.loader',
            'ansible.plugins.callback',
            'ansible.plugins.connection.ssh',
            'ansible.plugins.connection.local',
            'ansible.plugins.connection.docker',
            'ansible.plugins.connection.winrm',
            'ansible.plugins.become.sudo',
            'ansible.plugins.become.su',
            'ansible.utils.collection_loader._collection_finder',
            # gRPC internals
            'grpc._cython.cygrpc',
            # pkg_resources / importlib.metadata used by Ansible
            'pkg_resources',
            'importlib.metadata',
            'importlib.resources',
        ]
    ),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='terraform-provider-terrible',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    onefile=True,
)
