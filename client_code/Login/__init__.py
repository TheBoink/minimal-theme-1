from ._anvil_designer import LoginTemplate
from anvil import handle, open_form
import anvil.users


class Login(LoginTemplate):
    @handle('sign_in_button', 'click')
    def sign_in(self, **event_args):
        user = anvil.users.get_user()
        if user is None:
            user = anvil.users.login_with_form(show_signup_option=False, allow_cancel=True)
        if user is not None:
            open_form('Form1')
