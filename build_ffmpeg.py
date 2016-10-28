#!/usr/bin/python

import re
import os
import platform
import sys
import getopt
import shutil
import io
import argparse
import json
import urllib2
import subprocess
import textwrap
import traceback

from subprocess import *

BASE_PATH = os.path.abspath(os.path.dirname(sys.argv[0]))
proprietary_codecs = False


def main():
    nw_version = get_latest_stable_nwjs()
    host_platform = get_host_platform()
    target_arch = get_host_architecture()

    try:
        args = parse_args()
        if args.clean:
            response = raw_input('Are you sure you want to delete your workspace? (y/n):').lower()
            if response == 'y':
                print 'Cleaning workspace...'
                shutil.rmtree('build', ignore_errors=True)
            else:
                print 'Skipping workspace cleaning...'

        if args.nw_version:
            print 'Setting nw version to ' + args.nw_version
            nw_version = "v" + args.nw_version

        if args.target_arch:
            target_arch = args.target_arch

        if target_arch == 'ia32':
            target_cpu = 'x86'
        else:
            target_cpu = target_arch

        proprietary_codecs = args.proprietary_codecs
        if proprietary_codecs and platform.system() == 'Windows' and not 'CYGWIN_NT' in platform.system():
            print 'Script needs to be executed under CygWin to build FFmpeg \nwith proprietary codecs on Windows environments, \nread https://github.com/iteufel/nwjs-ffmpeg-prebuilt/blob/master/guides/build_windows.md\nExiting...'
            sys.exit(1)

        print 'Building ffmpeg for {0} on {1} for {2}, proprietary_codecs = {3}'.format(nw_version, host_platform, target_cpu, proprietary_codecs)

        create_build_directory()

        clean_output_directory()

        os.chdir('build')

        setup_chromium_depot_tools(nw_version)

        clone_chromium_source_code(nw_version)

        os.chdir('src')

        reset_chromium_src_to_nw_version(nw_version)

        generate_build_and_deps_files()

        install_build_deps()

        print 'Syncing with gclient...'
        os.system('gclient sync --no-history')

        check_build_with_proprietary_codecs(proprietary_codecs, host_platform, target_arch)

        print 'Generating ninja files...'
        subprocess.check_call('gn gen //out/nw "--args=is_debug=false is_component_ffmpeg=true target_cpu=\\\"%s\\\" is_official_build=true ffmpeg_branding=\\\"Chrome\\\""' % target_cpu, shell=True)

        print 'Starting ninja for building ffmpeg...'
        subprocess.check_call('ninja -C out/nw ffmpeg', shell=True)
    except KeyboardInterrupt:
        print "\n\nShutdown requested... exiting"
    except Exception:
        print traceback.format_exc()
        sys.exit(1)


def parse_args():
    parser = argparse.ArgumentParser(description='ffmpeg builder script.')
    parser.add_argument('-c', '--clean', help='Clean the workspace, removes downloaded source code', required=False, action='store_true')
    parser.add_argument('-nw', '--nw_version', help='Build ffmpeg for the specified Nw.js version', required=False)
    parser.add_argument('-ta', '--target_arch', help='Target architecture, ia32, x64', required=False)
    parser.add_argument('-pc', '--proprietary_codecs', help='Build ffmpeg with proprietary codecs', required=False, action='store_true')
    return parser.parse_args()


def grep_dep(reg, repo, dir, deps_str):
    pat = re.compile(reg)
    found = re.search(pat, deps_str)
    if found is None:
        return None
    head = found.group(1)
    return textwrap.dedent('''
      '%s':
        (Var(\"chromium_git\")) + '%s@%s',
    ''') % (dir, repo, head)


def get_host_platform():
    if platform.system() == 'Windows' or 'CYGWIN_NT' in platform.system():
        host_platform = 'win'
    elif platform.system() == 'Linux':
        host_platform = 'linux'
    elif platform.system() == 'Darwin':
        host_platform = 'mac'

    return host_platform


def get_host_architecture():
    if re.match(r'i.86', platform.machine()):
        host_arch = 'ia32'
    elif platform.machine() == 'x86_64' or platform.machine() == 'AMD64':
        host_arch = 'x64'
    elif platform.machine() == 'aarch64':
        host_arch = 'arm64'
    elif platform.machine().startswith('arm'):
        host_arch = 'arm'
    else:
        print 'Unexpected host machine architecture, exiting...'
        sys.exit(1)

    return host_arch


def get_latest_stable_nwjs():
    # We always build ffmpeg for the latest stable
    nwjs_io_url = 'http://nwjs.io/versions.json'
    try:
        versions = json.load(urllib2.urlopen(nwjs_io_url))
        nw_version = versions['stable']
    except URLError:
        nw_version = 'v0.18.0'
        print 'Error fetching ' + nwjs_io_url + ' URL, fall back to NW.js version ' + nw_version
    return nw_version


def create_build_directory():
    try:
        os.mkdir('build')
    except OSError:
        print 'Build directory is already created, skipping...'


def clean_output_directory():
    print 'Cleaning output directory...'
    shutil.rmtree('build/src/out', ignore_errors=True)


def setup_chromium_depot_tools(nw_version):
    if not os.path.isdir(os.getcwd() + '/depot_tools/.git'):
        print 'Cloning Chromium depot tools in {0}...'.format(os.getcwd())
        os.system('git clone --depth=1 https://chromium.googlesource.com/chromium/tools/depot_tools.git')

    sys.path.append(os.getcwd() + '/depot_tools')
    # fix for gclient not found, seems like sys.path.append does not work but
    # path is added
    os.environ["PATH"] += os.pathsep + os.getcwd() + "/depot_tools"
    if platform.system() == 'Windows' or 'CYGWIN_NT' in platform.system():
        os.environ["DEPOT_TOOLS_WIN_TOOLCHAIN"] = '0'

    print 'Creating .gclient file...'
    subprocess.check_call('gclient config --unmanaged --name=src https://github.com/nwjs/chromium.src.git@tags/nw-{0}'.format(nw_version), shell=True)


def clone_chromium_source_code(nw_version):
    print 'Cloning Chromium source code for nw-{0} in {1}'.format(nw_version, os.getcwd())
    os.system('git clone --depth=1 -b nw-{0} --single-branch {1} src'.format(
        nw_version, 'https://github.com/nwjs/chromium.src.git'))


def reset_chromium_src_to_nw_version(nw_version):
    print 'Hard source code reset to nw {0} specified version'.format(nw_version)
    os.system('git reset --hard tags/nw-{0}'.format(nw_version))


def get_min_deps(deps_str):
    # deps
    deps_list = {
        'buildtools': {
            'reg': ur'buildtools\.git@(.+)\'',
            'repo': '/chromium/buildtools.git',
            'path': 'src/buildtools'
        },
      'gyp': {
          'reg': ur'gyp\.git@(.+)\'',
          'repo': '/external/gyp.git',
          'path': 'src/tools/gyp'
      },
      'patched-yasm': {
          'reg': ur'patched-yasm\.git@(.+)\'',
          'repo': '/chromium/deps/yasm/patched-yasm.git',
          'path': 'src/third_party/yasm/source/patched-yasm'
      },
      'ffmpeg': {
          'reg': ur'ffmpeg\.git@(.+)\'',
          'repo': '/chromium/third_party/ffmpeg',
          'path': 'src/third_party/ffmpeg'
      },
    }
    min_deps_list = []
    for k, v in deps_list.items():
        dep = grep_dep(v['reg'], v['repo'], v['path'], deps_str)
        if dep is None:
            raise Exception("`%s` is not found in DEPS" % k)
        min_deps_list.append(dep)

    return textwrap.dedent('''
    deps = {
    %s
    }
    ''') % "\n".join(min_deps_list)


def get_min_vars():
    # vars
    # copied from DEPS
    return textwrap.dedent('''
    vars = {
      'chromium_git':
        'https://chromium.googlesource.com',
    }
    ''')


def get_min_hooks():
    # hooks
    # copied from DEPS
    return textwrap.dedent('''
    hooks = [
      {
        'action': [
          'python',
          'src/build/linux/sysroot_scripts/install-sysroot.py',
          '--running-as-hook'
        ],
        'pattern':
          '.',
        'name':
          'sysroot'
      },
      {
        'action': [
          'python',
          'src/build/mac_toolchain.py'
        ],
        'pattern':
          '.',
        'name':
          'mac_toolchain'
      },
      {
        'action': [
          'python',
          'src/tools/clang/scripts/update.py',
          '--if-needed'
        ],
        'pattern':
          '.',
        'name':
          'clang'
      },
      {
        'action': [
          'download_from_google_storage',
          '--no_resume',
          '--platform=win32',
          '--no_auth',
          '--bucket',
          'chromium-gn',
          '-s',
          'src/buildtools/win/gn.exe.sha1'
        ],
        'pattern':
          '.',
        'name':
          'gn_win'
      },
      {
        'action': [
          'download_from_google_storage',
          '--no_resume',
          '--platform=darwin',
          '--no_auth',
          '--bucket',
          'chromium-gn',
          '-s',
          'src/buildtools/mac/gn.sha1'
        ],
        'pattern':
          '.',
        'name':
          'gn_mac'
      },
      {
        'action': [
          'download_from_google_storage',
          '--no_resume',
          '--platform=linux*',
          '--no_auth',
          '--bucket',
          'chromium-gn',
          '-s',
          'src/buildtools/linux64/gn.sha1'
        ],
        'pattern':
          '.',
        'name':
          'gn_linux64'
      },
      {
        'action': [
          'download_from_google_storage',
          '--no_resume',
          '--platform=darwin',
          '--no_auth',
          '--bucket',
          'chromium-libcpp',
          '-s',
          'src/third_party/libc++-static/libc++.a.sha1'
        ],
        'pattern':
          '.',
        'name':
          'libcpp_mac'
      },
    ]
    ''')


def install_build_deps():
    # install linux dependencies
    if platform.system() == 'Linux' and not os.path.isfile('buid_deps.ok'):
        os.system('./build/install-build-deps.sh --no-prompt --no-nacl --no-chromeos-fonts --no-syms')
        with io.FileIO('buid_deps.ok', 'w') as file:
            file.write('Build dependencies already installed')


def delete_file(file_name):
    if os.path.exists(file_name):
        try:
            os.remove(file_name)
        except OSError as e:
            print ("Error: %s - %s." % (e.filename, e.strerror))


def generate_build_and_deps_files():
    print 'Cleaning previous DEPS and BUILD.gn backup files...'

    delete_file("DEPS.bak")
    delete_file("BUILD.gn.bak")

    with open('DEPS', 'r') as f:
        deps_str = f.read()

    print 'Backing up and overwriting DEPS...'
    shutil.move('DEPS', 'DEPS.bak')
    with open('DEPS', 'w') as f:
        f.write("%s\n%s\n%s" % (get_min_vars(), get_min_deps(deps_str), get_min_hooks()))

    print 'Backing up and overwriting BUILD.gn...'
    shutil.move('BUILD.gn', 'BUILD.gn.bak')

    BUILD_gn = textwrap.dedent('''
    group("dummy") {
      deps = [
        "//third_party/ffmpeg"
      ]
    }
    ''')

    with open('BUILD.gn', 'w') as f:
        f.write(BUILD_gn)


def cygwin_linking_setup():
    if 'CYGWIN_NT' in platform.system():
        if os.path.isfile('/usr/bin/link.exe'):
            print 'Overriding CygWin linker with MSVC linker...'
            shutil.move('/usr/bin/link.exe', '/usr/bin/link.exe.1')

        if not os.path.isfile('/usr/local/bin/cygwin-wrapper'):
            print 'Copying Cygwin wrapper...'
            shutil.copy(os.getcwd() + '/chromium/scripts/cygwin-wrapper', '/usr/local/bin/cygwin-wrapper')


def check_build_with_proprietary_codecs(proprietary_codecs, host_platform, target_arch):

    # going to ffmpeg folder
    os.chdir('third_party/ffmpeg')

    if proprietary_codecs:
        print 'Building ffmpeg with proprietary codecs...'
        if not os.path.isfile('build_ffmpeg_proprietary_codecs.patch'):
            print 'Applying codecs patch with ac3 for {0}...'.format(host_platform)
            shutil.copy(BASE_PATH + '/patch/' + host_platform + '/build_ffmpeg_proprietary_codecs.patch', os.getcwd())
            # apply codecs patch
            os.system('git apply --ignore-space-change --ignore-whitespace build_ffmpeg_proprietary_codecs.patch')

        cygwin_linking_setup()

        print 'Starting build...'

        # build ffmpeg
        subprocess.check_call('./chromium/scripts/build_ffmpeg.py {0} {1}'.format(host_platform, target_arch), shell=True)
        # copy the new generated ffmpeg config
        print 'Copying new ffmpeg configuration...'
        subprocess.call('./chromium/scripts/copy_config.sh', shell=True)
        print 'Creating a GYP include file for building FFmpeg from source...'
        # generate the ffmpeg configuration
        subprocess.check_call('./chromium/scripts/generate_gyp.py', shell=True)

        if 'CYGWIN_NT' in platform.system():
            print 'Applying fix for error LNK2001: unresolved external symbol _ff_w64_guid_data'
            fix_external_symbol_ff_w64_guid_data()
    else:
        if os.path.isfile('build_ffmpeg_proprietary_codecs.patch'):
            print 'Restoring ffmpeg configuration to defaults...'
            os.system('git clean -df')
            os.system('git checkout -- .')

    # back to src
    os.chdir('../..')

def replace_in_file(file_name, search_string, replace_string):
    filedata = None
    with open(file_name, 'r') as file :
      filedata = file.read()

    filedata = filedata.replace(search_string, replace_string)

    with open(file_name, 'w') as file :
      file.write(filedata)


def fix_external_symbol_ff_w64_guid_data():
    # https://bugs.chromium.org/p/chromium/issues/detail?id=264459
    shutil.copyfile('ffmpeg_generated.gni', 'ffmpeg_generated.gni.bak')
    shutil.copyfile('ffmpeg_generated.gypi', 'ffmpeg_generated.gypi.bak')
    replace = '''"libavformat/vorbiscomment.c",
    "libavformat/w64.c",'''
    replace_in_file('ffmpeg_generated.gni', '"libavformat/vorbiscomment.c",', replace)

    replace = ''''libavformat/vorbiscomment.c',
          'libavformat/w64.c','''
    replace_in_file('ffmpeg_generated.gypi', "'libavformat/vorbiscomment.c',", replace)


if __name__ == '__main__':
    sys.exit(main())
