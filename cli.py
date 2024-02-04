import click
from commands.keys import keys

@click.group()
def cli():
    pass


cli.add_command(keys)

if __name__ == '__main__':
    cli()
