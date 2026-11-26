from ._anvil_designer import UserAdminTemplate
from anvil import handle, open_form
import anvil.server


class UserAdmin(UserAdminTemplate):
    @handle('back_button', 'click')
    def back(self, **event_args):
        open_form('Form1')

    @handle('add_button', 'click')
    def add_user(self, **event_args):
        self.add_button.enabled = False
        try:
            error = anvil.server.call('add_user', self.email_input.text or '', self.key_input.text or '')
            self.status.text = error or 'User added. They can sign in using an email link and enter the pairing key.'
            if not error:
                self.email_input.text = ''
                self.key_input.text = ''
        finally:
            self.add_button.enabled = True
