"""Report CLI syntax errors without repeating the supplied command line."""
import argparse
import json


class LocalArgumentParser(argparse.ArgumentParser):
    def error(self, _message):
        self.reject_usage('CLI_ARGUMENTS_REJECTED')

    def reject_usage(self, code):
        result = {'status': 'REJECTED', 'error_code': code, 'help_command': f'{self.prog} --help'}
        self.exit(2, json.dumps(result) + '\n')
