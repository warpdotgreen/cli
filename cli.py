import click
from commands.keys import keys
from commands.deployment import deployment
from commands.listen import listen
from commands.rekey import rekey

@click.group()
def cli():
    pass


cli.add_command(keys)
cli.add_command(deployment)
cli.add_command(listen)
cli.add_command(rekey)

if __name__ == '__main__':
    cli()
