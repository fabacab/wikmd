import os
import datetime

from typing import Optional
from flask import Flask
from git import Repo, InvalidGitRepositoryError, GitCommandError, NoSuchPathError
from config import get_config


TEMP_DIR = "temp"
GIT_EMAIL_DEFAULT = "wikmd@no-mail.com"
GIT_USER_DEFAULT = "wikmd"
MAIN_BRANCH_NAME_DEFAULT = "main"

CONFIG = get_config()


def is_git_repo(path: str) -> bool:
    """
    Function that determines if the given path is a git repo.
    :return: True if is a repo, False otherwise.
    """
    try:
        _ = Repo(path).git_dir
        return True
    except (InvalidGitRepositoryError, NoSuchPathError):
        return False


def move_all_files(src_dir: str, dest_dir: str):
    """
    Function that moves all the files from a source directory to a destination one.
    If a file with the same name is already present in the destination, the source file will be renamed with a
    '-copy-XX' suffix.
    :param src_dir: source directory
    :param dest_dir: destination directory
    """
    if not os.path.isdir(dest_dir):
        os.mkdir(dest_dir)  # make the dir if it doesn't exists

    src_files = os.listdir(src_dir)
    dest_files = os.listdir(dest_dir)

    for file in src_files:
        new_file = file
        copies_count = 1
        while new_file in dest_files:  # if the file is already present, append '-copy-XX' to the file name
            file_split = file.split('.')
            new_file = f"{file_split[0]}-copy-{copies_count}"
            if len(file_split) > 1:  # if the file has an extension (it's not a directory nor a file without extension)
                new_file += f".{file_split[1]}"  # add the extension
            copies_count += 1

        os.rename(f"{src_dir}/{file}", f"{dest_dir}/{new_file}")


class WikiRepoManager:
    """
    Class that manages the git repo of the wiki.
    The repo could be local or remote (it will be cloned) depending on the config settings.
    """
    def __init__(self, flask_app: Flask):
        self.flask_app: Flask = flask_app

        self.wiki_directory = CONFIG["wiki_directory"]
        self.sync_with_remote = CONFIG["sync_with_remote"]
        self.remote_url = CONFIG["remote_url"]

        self.repo: Optional[Repo] = None
        self.__git_repo_init()

    def __git_repo_init(self):
        """
        Function that initializes the git repo of the wiki.
        """
        if is_git_repo(self.wiki_directory):
            self.__init_existing_repo()
        else:
            if self.remote_url:  # if a remote url has been set, clone the repo
                self.__init_remote_repo()
            else:
                self.__init_new_local_repo()

        # Configure git username and email
        if self.repo:  # if the repo has been initialized
            self.repo.config_writer().set_value("user", "name", GIT_USER_DEFAULT).release()
            self.repo.config_writer().set_value("user", "email", GIT_EMAIL_DEFAULT).release()

    def __init_existing_repo(self):
        """
        Function that inits the existing repo in the wiki_directory.
        Could be local or remote.
        """
        self.flask_app.logger.info(f"Initializing existing repo >>> {self.wiki_directory} ...")
        try:
            self.repo = Repo(self.wiki_directory)
            if not self.repo.branches:  # if the repo hasn't any branch yet
                self.__git_create_main_branch()
            self.repo.git.checkout()
        except (InvalidGitRepositoryError, GitCommandError, NoSuchPathError) as e:
            self.flask_app.logger.error(f"Existing repo initialization failed >>> {str(e)}")

    def __init_remote_repo(self):
        """
        Function that inits a remote git repo.
        The repo is cloned from the remote_url into the wiki_directory.
        Eventually, a 'main' branch is created if missing.
        """
        self.flask_app.logger.info(f"Cloning >>> {self.remote_url} ...")

        moved = False
        # if the wiki directory is not empty, move all the files into a 'temp' directory
        if os.listdir(self.wiki_directory):
            self.flask_app.logger.info(f"'{self.wiki_directory}' not empty, temporary moving them to 'temp' ...")
            move_all_files(self.wiki_directory, TEMP_DIR)
            moved = True

        try:
            self.repo = Repo.clone_from(url=self.remote_url, to_path=self.wiki_directory)  # git clone
        except (InvalidGitRepositoryError, GitCommandError, NoSuchPathError) as e:
            self.flask_app.logger.error(f"Cloning from remote repo failed >>> {str(e)}")

        if not self.repo.remotes:  # if the remote repo hasn't any branch yet
            self.__git_create_main_branch()

        self.repo.git.checkout()

        if moved:  # move back the files from the 'temp' directory
            move_all_files(TEMP_DIR, self.wiki_directory)
            os.rmdir(TEMP_DIR)
        self.flask_app.logger.info(f"Cloned repo >>> {self.remote_url}")

    def __init_new_local_repo(self):
        """
        Function that inits a new local git repo into the wiki_directory.
        It creates also the 'main' branch for the repo.
        """
        self.flask_app.logger.info(f"Creating a new local repo >>> {self.wiki_directory} ...")
        try:
            self.repo = Repo.init(path=self.wiki_directory)
            self.__git_create_main_branch()
        except (InvalidGitRepositoryError, GitCommandError, NoSuchPathError) as e:
            self.flask_app.logger.error(f"New local repo initialization failed >>> {str(e)}")

    def __git_create_main_branch(self):
        """
        Function that creates the 'main' branch for the wiki repo.
        The repo could be local or remote; in the latter case, local changes are pushed.
        """
        self.flask_app.logger.info(f"Creating 'main' branch ...")
        self.repo.git.checkout("-b", MAIN_BRANCH_NAME_DEFAULT)  # git checkout -b main
        self.__git_commit("First init commit")
        if self.sync_with_remote:
            self.__git_push()

    def __git_pull(self):
        """
        Function that pulls from the remote wiki repo.
        """
        self.flask_app.logger.info(f"Pulling from the repo >>> {self.wiki_directory} ...")
        try:
            self.repo.git.pull()  # git pull
        except Exception as e:
            self.flask_app.logger.info(f"git pull failed >>> {str(e)}")

    def __git_commit(self, message: str):
        """
        Function that makes a generic commit to the wiki repo.
        :param message: commit message.
        """
        try:
            self.repo.git.add("--all")  # git add --all
            self.repo.git.commit('-m', message)  # git commit -m
            self.flask_app.logger.info(f"New git commit >>> {message}")
        except Exception as e:
            self.flask_app.logger.error(f"git commit failed >>> {str(e)}")

    def __git_commit_page_changes(self, page_name: str = "", commit_type: str = ""):
        """
        Function that commits page changes to the wiki repo.
        :param commit_type: could be 'Add', 'Edit' or 'Remove'.
        :param page_name: name of the page that has been changed.
        """
        date = datetime.datetime.now()
        message = f"{commit_type} page '{page_name}' on {str(date)}"
        self.__git_commit(message=message)

    def __git_push(self):
        """
        Function that pushes changes to the remote wiki repo.
        It sets the upstream (param -u) to the active branch.
        """
        try:
            self.repo.git.push("-u", "origin", self.repo.active_branch)  # git push -u origin main|master
            self.flask_app.logger.info("Pushed to the repo.")
        except Exception as e:
            self.flask_app.logger.error(f"git push failed >>> {str(e)}")

    def git_sync(self, page_name: str = "", commit_type: str = ""):
        """
        Function that manages the synchronization with a git repo, that could be local or remote.
        If SYNC_WITH_REMOTE is set, it also pulls before committing and then pushes changes to the remote repo.
        :param commit_type: could be 'Add', 'Edit' or 'Remove'.
        :param page_name: name of the page that has been changed.
        """
        if self.sync_with_remote:
            self.__git_pull()

        self.__git_commit_page_changes(page_name=page_name, commit_type=commit_type)

        if self.sync_with_remote:
            self.__git_push()
