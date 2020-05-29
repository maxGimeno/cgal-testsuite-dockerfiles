from os import path
import logging
import docker
import podman
import re
import io

class TestsuiteException(Exception):
    pass

class TestsuiteWarning(TestsuiteException):
    """Warning exceptions thrown in this module."""
    def __init__(self, value):
        self.value = value
    def __str__(self):
        return 'Testsuite Warning: ' + repr(self.value)

class TestsuiteError(TestsuiteException):
    """Error exceptions thrown in this module."""
    def __init__(self, value):
        self.value = value
    def __str__(self):
        return 'Testsuite Error: ' + repr(self.value)

def container_by_id(podman_client, Id):
    """Returns a container given an `Id`. Raises `TestsuiteError` if the
    `Id` cannot be found."""
    contlist = [cont for cont in podman_client.containers(all=True) if Id == cont[u'Id']]
    if len(contlist) != 1:
        raise TestsuiteError('Requested Container Id ' + Id + 'does not exist')
    return contlist[0]

def images(podman_client, images):
    """If `images` is `None`, returns a list of default images, else
    validates the list of images and returns it. Raises an exception
    if an invalid image is found.
    """
    if not images:
        return _default_images(podman_client)
    not_existing = _not_existing_images(podman_client, images)
    for index in range(len(not_existing)):
      podman_client.images.pull(not_existing.pop())
    still_not_existing = _not_existing_images(podman_client, images)
    if len(still_not_existing) != 0:
        raise TestsuiteError('Could not find specified images: ' + ', '.join(still_not_existing))

    return images

def _default_images(podman_client):
    images = []
    for img in podman_client.images.list():
        tag = next((x for x in img.repoTags if x.startswith(u'cgal-testsuite/')), None)
        if tag:
            images.append(tag)
    return images

def _not_existing_images(podman_client, images):
    """Checks if each image in the list `images` is actually a name
    for a docker image. Returns a list of not existing names."""
    # Since img might contain a :TAG we need to work it a little.
    cl_images = [cl_img.repoTags[0].rsplit(':')[1] for cl_img in podman_client.images.list() if len(cl_img.repoTags) > 0]
    return [img for img in images if not img.rsplit(':')[1] in cl_images]

class ContainerRunner:
    # A regex to decompose the name of an image into the groups
    # ('user', 'name', 'tag')
    _image_name_regex = re.compile('(.*/)?([^:]*)(:.*)?')

    def __init__(self, podman_client, tester, tester_name,
                 tester_address, force_rm, nb_jobs, testsuite,
                 testresults, use_fedora_selinux_policy, intel_license, mac_address=None):
        assert path.isabs(testsuite.path), 'testsuite needs to be an absolute path'
        assert path.isabs(testresults), 'testresults needs to be an absolute path'
        self.podman_client = podman_client
        self.force_rm = force_rm
        self.use_fedora_selinux_policy = use_fedora_selinux_policy
        self.environment=["CGAL_TESTER="+tester,
                          "CGAL_TESTER_NAME="+tester_name,
                          "CGAL_TESTER_ADDRESS="+tester_address,
                          "CGAL_NUMBER_OF_JOBS={}".format(nb_jobs)]
        self.bind=['type=bind,src='+testsuite.path+',target=/mnt/testsuite,ro=True',
        'type=bind,src='+testresults+',target=/mnt/testresults,ro=False']
        if intel_license and path.isdir(intel_license):
            assert path.isabs(intel_license), 'intel_license needs to be an absolute path'
            self.bind.append('type=bind,src='+intel_license+',target=/opt/intel/licenses,ro=True')
            logging.info('Using {} as intel license directory'.format(intel_license))
        else:
            logging.info('Not using an intel license directory')


        self.mac_address = mac_address
        if mac_address:
            logging.info('Using custom MAC address: {}'.format(mac_address))
        else:
            logging.info('Not using custom MAC address')

    def run(self, image):
        """Create and start a container of the `image`."""

        cont = self._create_container(image)
        logging.info('Created container: {0} with id {1} from image {2}'
                     .format(cont['names'], cont['id'], cont.inspect()[6]))
        cont.start()
        return cont._id

    def _create_container(self, img):
        # This is a bit wacky since names can be basically anything but we expect two kinds of names:
        # (docker.io/)cgal-testsuite/PLATFORM or cgal/testsuite-docker:PLATFORM
        res = self._image_name_regex.search(img)
        if 'testsuite-docker' in res.group(2):
            chosen_name = 'CGAL-{}-testsuite'.format(res.group(3)[1:])
        elif res.group(3):
            chosen_name = 'CGAL-{}-{}-testsuite'.format(res.group(2), res.group(3)[1:])
        else:
            chosen_name = 'CGAL-{}-testsuite'.format(res.group(2))

        existing = [cont for cont in self.podman_client.containers.list() if '/' + chosen_name in cont['names']]
        assert len(existing) == 0 or len(existing) == 1, 'Length of existing containers is odd'

        if len(existing) != 0 and 'exited' in existing[0]['status']:
            logging.info('An Exited container with name {} already exists. Removing.'.format(chosen_name))
            [cont.remove() for cont in self.podman_client.containers.list() if cont['names'] == chosen_name]
        elif len(existing) != 0 and self.force_rm:
            logging.info('A non-Exited container with name {} already exists. Forcing exit and removal.'.format(chosen_name))
            [cont.remove(True) for cont in self.podman_client.containers.list() if cont['names'] == chosen_name]
        elif len(existing) != 0:
            raise TestsuiteWarning('A non-Exited container with name {} already exists. Skipping.'.format(chosen_name))

        cimg = [im for im in self.podman_client.images.list() if im.repoTags[0].rsplit(':')[1] == img.rsplit(':')[1] ]
        if len(cimg) == 0 :
          raise TestsuiteError('missing image ' + img)
        print("img = ", img)
        print("chosen_name = ", chosen_name)
        container = cimg[0].create(name=chosen_name,
            entrypoint='/mnt/testsuite/docker-entrypoint.sh',
            env=self.environment,
            mount=self.bind
        )
        #.create_container(
        #    image=cimg,
        #    name=chosen_name,
        #    entrypoint=['/mnt/testsuite/docker-entrypoint.sh'],
        #    volumes=['/mnt/testsuite', '/mnt/testresults'],
        #    environment=self.environment,
        #    host_config=config,
        #    mac_address=self.mac_address
        #)
        return container

class ContainerScheduler:
    def __init__(self, runner, images):
        self.runner = runner
        self.images = images
        self.running_containers = []
        # error handling
        self.errors_encountered = False
        self.error_buffer=io.StringIO()
        self.error_handler=logging.StreamHandler(self.error_buffer)
        self.error_handler.setFormatter( logging.Formatter('%(levelname)s: %(message)s') )
        self.error_logger = logging.getLogger("Error_logger")
        self.error_logger.addHandler(self.error_handler)

    def launch(self):
        """Launch as many containers as possible. Returns `False` if all
        images have been scheduled, `True` otherwise."""

        nb_images = len(self.images)

        if len(self.images) == 0:
            return False

        while self.images:
            image_to_launch = self.images.pop()
            run_no_exception=True
            try:
              cont_id = self.runner.run(image_to_launch)
            except TestsuiteWarning as e:
                logging.warning(e.value)
            #won't happen with podman
            except docker.errors.APIError as e:
                run_no_exception=False
                self.errors_encountered = True
                logging.error("There was a fatal error while trying to run testsuite on "+image_to_launch)
                logging.error("Error message is: "+e.explanation)
                self.error_logger.error("There was a fatal error while trying to run testsuite on "+image_to_launch)
                self.error_logger.error("Error message is: "+e.explanation)
                nb_images-=1
                if nb_images==0:
                  return False
            if run_no_exception:
                self.running_containers.append(cont_id)

        return True


    def is_ours(self, container_id):
        """Return `True` if the container specified by `container_id` has been
        launched by this scheduler, `False` otherwise."""
        return container_id in self.running_containers

    def containers_running(self):
        """Return `True` if there are still containers running, that have been
        launched by this scheduler, `False` otherwise."""
        return len(self.running_containers) != 0

    def kill_all(self):
        """Kill all still running containers."""
        for cont in self.running_containers:
            logging.info('Killing Container ID {}'.format(cont))
            self.runner.podman_client.containers.get(cont).kill(True)

if __name__ == "__main__":
    pass
