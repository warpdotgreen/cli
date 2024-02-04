import click
from commands.keys import keys
from commands.deployment import deployment

@click.group()
def cli():
    pass


cli.add_command(keys)
cli.add_command(deployment)

if __name__ == '__main__':
    cli()
