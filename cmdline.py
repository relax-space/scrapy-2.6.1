import argparse
import cProfile
import inspect
import os
import sys

import pkg_resources

import scrapy
from scrapy.commands import ScrapyCommand, ScrapyHelpFormatter
from scrapy.crawler import CrawlerProcess
from scrapy.exceptions import UsageError
from scrapy.utils.misc import walk_modules
from scrapy.utils.project import get_project_settings, inside_project
from scrapy.utils.python import garbage_collect


def _iter_command_classes(module_name):
    # TODO: add `name` attribute to commands and and merge this function with
    # scrapy.utils.spider.iter_spider_classes
    for module in walk_modules(module_name):
        for obj in vars(module).values():
            if (inspect.isclass(obj) and issubclass(obj, ScrapyCommand)
                    and obj.__module__ == module.__name__
                    and not obj == ScrapyCommand):
                yield obj


def _get_commands_from_module(module, inproject):
    d = {}
    for cmd in _iter_command_classes(module):
        if inproject or not cmd.requires_project:
            cmdname = cmd.__module__.split('.')[-1]
            d[cmdname] = cmd()
    return d


def _get_commands_from_entry_points(inproject, group='scrapy.commands'):
    cmds = {}
    for entry_point in pkg_resources.iter_entry_points(group):
        obj = entry_point.load()
        if inspect.isclass(obj):
            cmds[entry_point.name] = obj()
        else:
            raise Exception(f"Invalid entry point {entry_point.name}")
    return cmds


def _get_commands_dict(settings, inproject):
    cmds = _get_commands_from_module('scrapy.commands', inproject)
    cmds.update(_get_commands_from_entry_points(inproject))
    cmds_module = settings['COMMANDS_MODULE']
    if cmds_module:
        cmds.update(_get_commands_from_module(cmds_module, inproject))
    return cmds


def _pop_command_name(argv):
    i = 0
    for arg in argv[1:]:
        if not arg.startswith('-'):
            del argv[i]
            return arg
        i += 1


def _print_header(settings, inproject):
    version = scrapy.__version__
    if inproject:
        print(f"Scrapy {version} - project: {settings['BOT_NAME']}\n")
    else:
        print(f"Scrapy {version} - no active project\n")


def _print_commands(settings, inproject):
    _print_header(settings, inproject)
    print("Usage:")
    print("  scrapy <command> [options] [args]\n")
    print("Available commands:")
    cmds = _get_commands_dict(settings, inproject)
    for cmdname, cmdclass in sorted(cmds.items()):
        print(f"  {cmdname:<13} {cmdclass.short_desc()}")
    if not inproject:
        print()
        print(
            "  [ more ]      More commands available when run from project directory"
        )
    print()
    print('Use "scrapy <command> -h" to see more info about a command')


def _print_unknown_command(settings, cmdname, inproject):
    _print_header(settings, inproject)
    print(f"Unknown command: {cmdname}\n")
    print('Use "scrapy" to see available commands')


def _run_print_help(parser, func, *a, **kw):
    try:
        func(*a, **kw)
    except UsageError as e:
        if str(e):
            parser.error(str(e))
        if e.print_help:
            parser.print_help()
        sys.exit(2)


def execute(argv=None, settings=None):
    # argv是 ['scrapy', 'crawl', 'gitee-project'], gitee-project是自定义爬虫的name
    if argv is None:
        argv = sys.argv

    if settings is None:
        '''
        get_project_settings():
        1. 设置环境变量: 从scapy.cfg中读取settings.py的路径, 并放到环境变量中os.environ['SCRAPY_SETTINGS_MODULE']
        2. settings变量: 从settings.py读取数据到settings变量
        '''
        settings = get_project_settings()
        # set EDITOR from environment if available
        try:
            editor = os.environ['EDITOR']
        except KeyError:
            pass
        else:
            settings['EDITOR'] = editor
    # 确保 setting.py文件存在于项目中,并且能正确导入
    inproject = inside_project()
    # 获取scrapy.commands模块中, 所有继承ScrapyCommand类的实例
    cmds = _get_commands_dict(settings, inproject)
    cmdname = _pop_command_name(argv)
    # 执行完上面一句之后: cmdname: crawl, argv: ['crawl', 'gitee-project']
    if not cmdname:
        _print_commands(settings, inproject)
        sys.exit(0)
    elif cmdname not in cmds:
        _print_unknown_command(settings, cmdname, inproject)
        sys.exit(2)

    cmd = cmds[cmdname]
    parser = argparse.ArgumentParser(formatter_class=ScrapyHelpFormatter,
                                     usage=f"scrapy {cmdname} {cmd.syntax()}",
                                     conflict_handler='resolve',
                                     description=cmd.long_desc())
    settings.setdict(cmd.default_settings, priority='command')
    cmd.settings = settings
    cmd.add_options(parser)
    # args: ['gitee-project']
    opts, args = parser.parse_known_args(args=argv[1:])
    _run_print_help(parser, cmd.process_options, args, opts)

    # 初始化spider_loader,同时也初始化spider_loader._spiders, 程序入口: super().__init__(settings)
    cmd.crawler_process = CrawlerProcess(settings)
    # 执行:_run_command
    _run_print_help(parser, _run_command, cmd, args, opts)
    sys.exit(cmd.exitcode)


def _run_command(cmd, args, opts):
    if opts.profile:
        _run_command_profiled(cmd, args, opts)
    else:
        # gogo scrapy.commands.crawl.Command的run方法,参数args: ['gitee-project']
        cmd.run(args, opts)


def _run_command_profiled(cmd, args, opts):
    if opts.profile:
        sys.stderr.write(
            f"scrapy: writing cProfile stats to {opts.profile!r}\n")
    loc = locals()
    p = cProfile.Profile()
    p.runctx('cmd.run(args, opts)', globals(), loc)
    if opts.profile:
        p.dump_stats(opts.profile)


if __name__ == '__main__':
    try:
        execute()
    finally:
        # Twisted prints errors in DebugInfo.__del__, but PyPy does not run gc.collect() on exit:
        # http://doc.pypy.org/en/latest/cpython_differences.html
        # ?highlight=gc.collect#differences-related-to-garbage-collection-strategies
        garbage_collect()
