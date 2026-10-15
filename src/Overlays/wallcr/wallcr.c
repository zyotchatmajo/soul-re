#include "Overlays/wallcr/wallcr.h"
#include "Game/MONSTER/MONAPI.h"
#include "Game/MONSTER/MONSTER.h"
#include "Game/PHYSICS.h"

/* double-check that WALLCR_ShouldJumpOff and WALLCR_ShouldJumpOn aren't swapped */

// this conditional is for the objdiff report
#ifndef SKIP_ASM

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_EnableControllers);

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_DisableControllers);

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_SetFlex);

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_CollideFace);

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_NearFloor);

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_FindJumpOnPoint);

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_AngleFromPosToPos);

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_FollowWall);

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_MoveForward);

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_ApplySpin);

INCLUDE_RODATA("asm/nonmatchings/Overlays/wallcr/wallcr", D_88000000);

INCLUDE_RODATA("asm/nonmatchings/Overlays/wallcr/wallcr", D_88000020);

INCLUDE_RODATA("asm/nonmatchings/Overlays/wallcr/wallcr", D_88000038);

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_AttachEntry);

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_Attach);

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_DetachEntry);

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_Detach);

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_SeekPath);

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_ShouldJumpOff);

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_ShouldJumpOn);

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_ShouldSurpriseAttack);

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_MoveToDestination);

void WALLCR_Message(Instance *instance, unsigned long message, unsigned long data)
{
    MonsterMessage(instance, message, data);
}

uintptr_t WALLCR_Query(Instance *instance, unsigned long query)
{
    uintptr_t ret;

    if (query == 13)
    {
        if (instance->matrix != NULL)
        {
            ret = (uintptr_t)instance->matrix;
        }
        else
        {
            ret = 0;
        }
    }
    else
    {
        ret = MonsterQuery(instance, query);
    }

    return ret;
}

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_Init);

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_CleanUp);

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_PursueEntry);

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_Pursue);

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_SurpriseAttackEntry);

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_SurpriseAttack);

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_IdleEntry);

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_ShouldIAmbushPlayer);

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_Idle);

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_WanderEntry);

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_Wander);

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_FallEntry);

void WALLCR_Fall(Instance *instance)
{
    MON_Fall(instance);
}

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_ImpactEntry);

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_Impact);

void WALLCR_GeneralDeathEntry(Instance *instance)
{
    MonsterVars *mv = (MonsterVars *)instance->extraData;

    WALLCR_DisableControllers(instance);
    mv->mvFlags &= ~0x800;
    mv->auxFlags &= ~4;
    instance->zAccl = -16;
    instance->rotation.y = 0;
    instance->rotation.x = 0;
    MON_GeneralDeathEntry(instance);
}

void WALLCR_GeneralDeath(Instance *instance)
{
    MON_GeneralDeath(instance);
}

void WALLCR_HitEntry(Instance *instance)
{
    MonsterVars *mv = (MonsterVars *)instance->extraData;

    WALLCR_DisableControllers(instance);
    mv->mvFlags &= ~0x800;
    mv->auxFlags &= ~4;
    instance->zAccl = -16;
    instance->rotation.y = 0;
    instance->rotation.x = 0;
    MON_HitEntry(instance);
}

void WALLCR_Hit(Instance *instance)
{
    MON_Hit(instance);
}

void WALLCR_MissileHitEntry(Instance *instance)
{
    MonsterVars *mv = (MonsterVars *)instance->extraData;

    WALLCR_DisableControllers(instance);
    mv->mvFlags &= ~0x800;
    mv->auxFlags &= ~4;
    instance->zAccl = -16;
    instance->rotation.y = 0;
    instance->rotation.x = 0;
    MON_MissileHitEntry(instance);
}

void WALLCR_MissileHit(Instance *instance)
{
    MON_MissileHit(instance);
}

void WALLCR_NoticeEntry(Instance *instance)
{
    MonsterVars *mv = (MonsterVars *)instance->extraData;

    WALLCR_DisableControllers(instance);
    mv->mvFlags &= ~0x800;
    mv->auxFlags &= ~4;
    instance->zAccl = -16;
    instance->rotation.y = 0;
    instance->rotation.x = 0;
    MON_NoticeEntry(instance);
}

void WALLCR_Notice(Instance *instance)
{
    MON_Notice(instance);
}

void WALLCR_SurprisedEntry(Instance *instance)
{
    MonsterVars *mv = (MonsterVars *)instance->extraData;

    WALLCR_DisableControllers(instance);
    mv->mvFlags &= ~0x800;
    mv->auxFlags &= ~4;
    instance->zAccl = -16;
    instance->rotation.y = 0;
    instance->rotation.x = 0;
    MON_SurprisedEntry(instance);
}

void WALLCR_Surprised(Instance *instance)
{
    MON_Surprised(instance);
}

void WALLCR_StunnedEntry(Instance *instance)
{
    MonsterVars *mv = (MonsterVars *)instance->extraData;

    WALLCR_DisableControllers(instance);
    mv->mvFlags &= ~0x800;
    mv->auxFlags &= ~4;
    instance->zAccl = -16;
    instance->rotation.y = 0;
    instance->rotation.x = 0;
    MON_StunnedEntry(instance);
}

void WALLCR_Stunned(Instance *instance)
{
    MON_Stunned(instance);
}

void WALLCR_ThrownEntry(Instance *instance)
{
    MonsterVars *mv = (MonsterVars *)instance->extraData;

    WALLCR_DisableControllers(instance);
    mv->mvFlags &= ~0x800;
    mv->auxFlags &= ~4;
    instance->zAccl = -16;
    instance->rotation.y = 0;
    instance->rotation.x = 0;
    MON_ThrownEntry(instance);
}

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_Thrown);

void WALLCR_PupateEntry(Instance *instance)
{
    MON_PupateEntry(instance);
}

void WALLCR_Pupate(Instance *instance)
{
    MON_Pupate(instance);

    if (instance->currentMainState != MONSTER_STATE_PUPATE)
    {
        PhysicsSetVelFromRot(instance, &instance->rotation, 15);
    }
}

#else

void WALLCR_EnableControllers(void) { };

void WALLCR_DisableControllers(void) { };

void WALLCR_SetFlex(void) { };

void WALLCR_CollideFace(void) { };

void WALLCR_NearFloor(void) { };

void WALLCR_FindJumpOnPoint(void) { };

void WALLCR_AngleFromPosToPos(void) { };

void WALLCR_FollowWall(void) { };

void WALLCR_MoveForward(void) { };

void WALLCR_ApplySpin(void) { };

void WALLCR_AttachEntry(void) { };

void WALLCR_Attach(void) { };

void WALLCR_DetachEntry(void) { };

void WALLCR_Detach(void) { };

void WALLCR_SeekPath(void) { };

void WALLCR_ShouldJumpOff(void) { };

void WALLCR_ShouldJumpOn(void) { };

void WALLCR_ShouldSurpriseAttack(void) { };

void WALLCR_MoveToDestination(void) { };

void WALLCR_Message(void) { };

void WALLCR_Query(void) { };

void WALLCR_Init(void) { };

void WALLCR_CleanUp(void) { };

void WALLCR_PursueEntry(void) { };

void WALLCR_Pursue(void) { };

void WALLCR_SurpriseAttackEntry(void) { };

void WALLCR_SurpriseAttack(void) { };

void WALLCR_IdleEntry(void) { };

void WALLCR_ShouldIAmbushPlayer(void) { };

void WALLCR_Idle(void) { };

void WALLCR_WanderEntry(void) { };

void WALLCR_Wander(void) { };

void WALLCR_FallEntry(void) { };

void WALLCR_Fall(void) { };

void WALLCR_ImpactEntry(void) { };

void WALLCR_Impact(void) { };

void WALLCR_GeneralDeathEntry(void) { };

void WALLCR_GeneralDeath(void) { };

void WALLCR_HitEntry(void) { };

void WALLCR_Hit(void) { };

void WALLCR_MissileHitEntry(void) { };

void WALLCR_MissileHit(void) { };

void WALLCR_NoticeEntry(void) { };

void WALLCR_Notice(void) { };

void WALLCR_SurprisedEntry(void) { };

void WALLCR_Surprised(void) { };

void WALLCR_StunnedEntry(void) { };

void WALLCR_Stunned(void) { };

void WALLCR_ThrownEntry(void) { };

void WALLCR_Thrown(void) { };

void WALLCR_PupateEntry(void) { };

void WALLCR_Pupate(void) { };

#endif
