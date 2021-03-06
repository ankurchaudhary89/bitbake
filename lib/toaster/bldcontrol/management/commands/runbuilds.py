from django.core.management.base import NoArgsCommand, CommandError
from django.db import transaction
from orm.models import Build
from bldcontrol.bbcontroller import getBuildEnvironmentController, ShellCmdException, BuildSetupException
from bldcontrol.models import BuildRequest, BuildEnvironment, BRError
import os
import logging

logger = logging.getLogger("toaster")

class Command(NoArgsCommand):
    args    = ""
    help    = "Schedules and executes build requests as possible. Does not return (interrupt with Ctrl-C)"


    @transaction.commit_on_success
    def _selectBuildEnvironment(self):
        bec = getBuildEnvironmentController(lock = BuildEnvironment.LOCK_FREE)
        bec.be.lock = BuildEnvironment.LOCK_LOCK
        bec.be.save()
        return bec

    @transaction.commit_on_success
    def _selectBuildRequest(self):
        br = BuildRequest.objects.filter(state = BuildRequest.REQ_QUEUED).order_by('pk')[0]
        br.state = BuildRequest.REQ_INPROGRESS
        br.save()
        return br

    def schedule(self):
        import traceback
        try:
            br = None
            try:
                # select the build environment and the request to build
                br = self._selectBuildRequest()
            except IndexError as e:
                # logger.debug("runbuilds: No build request")
                return
            try:
                bec = self._selectBuildEnvironment()
            except IndexError as e:
                # we could not find a BEC; postpone the BR
                br.state = BuildRequest.REQ_QUEUED
                br.save()
                logger.debug("runbuilds: No build env")
                return

            logger.debug("runbuilds: starting build %s, environment %s" % (br, bec.be))
            # let the build request know where it is being executed
            br.environment = bec.be
            br.save()

            # set up the buid environment with the needed layers
            bec.setLayers(br.brbitbake_set.all(), br.brlayer_set.all())
            bec.writePreConfFile(br.brvariable_set.all())

            # get the bb server running with the build req id and build env id
            bbctrl = bec.getBBController("%d:%d" % (br.pk, bec.be.pk))

            # trigger the build command
            task = reduce(lambda x, y: x if len(y)== 0 else y, map(lambda y: y.task, br.brtarget_set.all()))
            if len(task) == 0:
                task = None
            bbctrl.build(list(map(lambda x:x.target, br.brtarget_set.all())), task)

            logger.debug("runbuilds: Build launched, exiting")
            # disconnect from the server
            bbctrl.disconnect()

            # cleanup to be performed by toaster when the deed is done


        except Exception as e:
            logger.error("runbuilds: Error executing shell command %s" % e)
            traceback.print_exc(e)
            BRError.objects.create(req = br,
                errtype = str(type(e)),
                errmsg = str(e),
                traceback = traceback.format_exc(e))
            br.state = BuildRequest.REQ_FAILED
            br.save()
            bec.be.lock = BuildEnvironment.LOCK_FREE
            bec.be.save()



    def cleanup(self):
        from django.utils import timezone
        from datetime import timedelta
        # environments locked for more than 30 seconds - they should be unlocked
        BuildEnvironment.objects.filter(lock=BuildEnvironment.LOCK_LOCK).filter(updated__lt = timezone.now() - timedelta(seconds = 30)).update(lock = BuildEnvironment.LOCK_FREE)


    def handle_noargs(self, **options):
        self.cleanup()
        self.schedule()
