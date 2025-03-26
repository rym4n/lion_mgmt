# coding=utf-8

import os
import shutil
import traceback
from distutils.command.clean import clean
from distutils.extension import Extension

from Cython.Distutils import build_ext
from setuptools import setup


def load_all(root):
    out = []
    if os.path.isdir(root):
        out.append((root, True))
        names = os.listdir(root)
        for name in names:
            p = os.path.join(root, name)
            if os.path.isdir(p):
                out += load_all(p)
            else:
                out.append((p, False))
    else:
        out.append((root, False))
    return out


def load_files(root, exts=("py",)):
    out = []
    for (p, is_dir) in load_all(root):
        if not is_dir:
            ext = p.split(".")
            if len(ext) > 1 and ext[-1] in exts:
                out.append(p)
    return out


def get_packages(root):
    out = []
    for (p, is_dir) in load_all(root):
        if is_dir:
            out.append(p)
    return out


def get_extensions(root):
    py_files = load_files(root, exts=("py",))
    py_files = filter(lambda x: '__' not in x, py_files)
    ext_names = map(lambda x: x.replace(os.path.sep, '.')[:-3], py_files)

    def make_extension(ext_name):
        ext_path = ext_name.replace('.', os.path.sep) + '.py'
        ext = Extension(ext_name, [ext_path], include_dirs=['.'])
        ext.cython_directives = {'language_level': "3"}
        return ext

    extensions = map(lambda x: make_extension(x), ext_names)
    return list(extensions)


# 对加密后的py，pyc和c文件进行清除
class CleanCode(object):

    def clean_build(self, distribution):
        clean_command = clean(distribution)
        clean_command.all = True
        clean_command.finalize_options()
        clean_command.run()

    def delete(self, root, exts):
        src_files = load_files(root, exts=exts)
        src_files = filter(lambda x: not x.endswith('__init__.py'), src_files)
        for source_file in src_files:
            if os.path.basename(source_file):
                os.remove(source_file)

    def rename(self, root, exts):
        bin_files = load_files(root, exts=exts)
        for bin_file in bin_files:
            old_pyd = bin_file.split(".")
            new_pyd = str(old_pyd[0]) + "." + str(old_pyd[2])
            os.rename(bin_file, new_pyd)

    def clean_pro(self, root):
        self.delete(root, exts=("pyd", "pyc", "c", "so", "env"))

    def delete_source_code(self, root):
        self.delete(root, exts=("py", "c"))


TARGET_DIR = "dist"


class CustomBuildExt(build_ext, CleanCode):

    def run(self):
        self.clean_pro(TARGET_DIR)
        build_ext.run(self)
        self.clean_build(self.distribution)
        self.rename(TARGET_DIR, exts=("pyd", "so"))
        self.delete_source_code(TARGET_DIR)


if __name__ == "__main__":
    try:
        shutil.rmtree(TARGET_DIR, ignore_errors=True)

        excludes = ["dist", "data", "logs", "migrations"]

        targets = []
        # 罗列当前目录下的所有目录，如果子目录名称在excludes中，则忽略，否则加入到targets，并且复制到TARGET_DIR目录下
        subdirectories = [d for d in os.listdir('.') if os.path.isdir(d)]
        for subdir in subdirectories:
            if subdir not in excludes:
                targets.append(subdir)
                shutil.copytree(subdir, os.path.join(TARGET_DIR, subdir))

        setup(
            name="lion_mgmt",
            packages=get_packages(TARGET_DIR),
            ext_modules=get_extensions(TARGET_DIR),
            script_args=["build_ext", "--inplace"],
            cmdclass={'build_ext': CustomBuildExt}
        )
        c = CleanCode()
        for d in targets:
            c.delete(d, exts=("pyd", "pyc", "c", "so"))
    except Exception as e:
        print(f"{e} | {traceback.format_exc()}")
