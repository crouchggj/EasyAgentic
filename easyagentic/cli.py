"""Terminal input/output and error presentation for the assembled application."""
from .bootstrap import create_application
from .commands import CommandRouter


def run_cli(application, *, input_fn=None, output_fn=None):
    input_fn = input if input_fn is None else input_fn
    output_fn = print if output_fn is None else output_fn
    skills = application.skills.get_skill_names()
    output_fn(f'Loaded {len(skills)} skills: ' + ', '.join(skills))
    output_fn('Simple Agent - /reviews, /review ID, /approve ID, /reject ID; /resume, /continue, /reset, quit')
    router = CommandRouter(application)
    while True:
        query = ''
        try:
            query = input_fn('\nYou: ').strip()
            result = router.handle(query)
            if result.output is not None:
                output_fn(result.output)
            if result.exit_requested:
                break
            if not result.handled:
                output_fn('\nFinal Answer:\n' + application.agent.run(result.query))
        except EOFError:
            output_fn('Goodbye!')
            break
        except KeyboardInterrupt:
            output_fn('Task interrupted; use /continue or /reset, or quit.')
        except (ValueError, FileNotFoundError) as exc:
            if query.startswith(('/review', '/approve', '/reject', '/message-retry', '/team-resume')):
                output_fn(f'Command refused: {exc}')
            else:
                output_fn(f'Request failed ({type(exc).__name__}); use /continue after resolving the cause.')
        except Exception as exc:
            # API error bodies may contain credentials or request data.
            output_fn(f'Request failed ({type(exc).__name__}); use /continue after resolving the cause.')


def main():
    with create_application() as application:
        run_cli(application)
