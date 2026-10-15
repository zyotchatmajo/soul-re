#include "Overlays/walboss/walboss.h"
#include "Game/G2/ANMCTRLR.h"
#include "Game/MATH3D.h"
#include "Game/GAMELOOP.h"
#include "Game/INSTANCE.h"

// this conditional is for the objdiff report
#ifndef SKIP_ASM

void WALBOSS_SetSegmentYaw(Instance *instance, int yaw)
{
    MonsterVars *mv;
    MonsterAttributes *ma;
    WalbossVars *vars;
    WalbossAttributes *attrs;
    G2SVector3 rotation;
    register int value asm("v1");
    int result;

    mv = (MonsterVars *)instance->extraData;
    ma = (MonsterAttributes *)instance->data;
    vars = (WalbossVars *)mv->extraVars;
    attrs = (WalbossAttributes *)ma->tunData;
    if (vars != NULL && attrs != NULL)
    {
        yaw &= 0xFFF;
        vars->segmentYaw = yaw;
        rotation.y = 0;
        rotation.x = 0;
        if (yaw < 0x801)
        {
            value = yaw * 5;
        }
        else
        {
            value = (yaw - 0x1000) * 5;
        }
        result = value * 15;
        __asm__ volatile ("sll %0, %1, 3\n\tsra %0, %0, 10" : "=r"(rotation.z) : "r"(result));
        rotation.z &= 0xFFF;
        G2Anim_SetController_Vector(&instance->anim, *(short *)((char *)attrs + 8), G2ANIM_CTRLRTYPE_ADD_LOCALROT, &rotation);
    }
}

INCLUDE_ASM("asm/nonmatchings/Overlays/walboss/walboss", WALBOSS_InterpSegmentYaw);

void WALBOSS_TurnToPosition(Instance *instance, Position *target)
{
    int angle;
    angle = MATH3D_AngleFromPosToPos(&instance->position, target);
    angle = (short)angle - instance->rotation.z;
    WALBOSS_InterpSegmentYaw(instance, angle & 0xFFF);
}

INCLUDE_ASM("asm/nonmatchings/Overlays/walboss/walboss", WALBOSS_ChooseAttack);

INCLUDE_ASM("asm/nonmatchings/Overlays/walboss/walboss", WALBOSS_ShouldIAttack);

void WALBOSS_MessageToLegs(int message)
{
    Instance *inst;
    for (inst = gameTrackerX.instanceList->first; inst != NULL; inst = inst->next)
    {
        if (INSTANCE_Query(inst, 1) == 0x410002)
            INSTANCE_Post(inst, 0x01000017, message);
    }
}

__asm__(
    ".section .text\n"
    ".set noat\n"
    ".set noreorder\n"
    ".globl WALBOSS_LivingLegs\n"
    ".type WALBOSS_LivingLegs, @function\n"
    "WALBOSS_LivingLegs:\n"
    ".include \"asm/nonmatchings/Overlays/walboss/walboss/WALBOSS_LivingLegs.s\"\n"
    ".set reorder\n"
    ".set at\n"
);

__asm__(
    ".section .text\n"
    ".set noat\n"
    ".set noreorder\n"
    ".globl WALBOSS_FindEgg\n"
    ".type WALBOSS_FindEgg, @function\n"
    "WALBOSS_FindEgg:\n"
    ".include \"asm/nonmatchings/Overlays/walboss/walboss/WALBOSS_FindEgg.s\"\n"
    ".set reorder\n"
    ".set at\n"
);

__asm__(
    ".section .text\n"
    ".set noat\n"
    ".set noreorder\n"
    ".globl WALBOSS_CreateEgg\n"
    ".type WALBOSS_CreateEgg, @function\n"
    "WALBOSS_CreateEgg:\n"
    ".include \"asm/nonmatchings/Overlays/walboss/walboss/WALBOSS_CreateEgg.s\"\n"
    ".set reorder\n"
    ".set at\n"
);

__asm__(
    ".section .text\n"
    ".set noat\n"
    ".set noreorder\n"
    ".globl WALBOSS_EjectEgg\n"
    ".type WALBOSS_EjectEgg, @function\n"
    "WALBOSS_EjectEgg:\n"
    ".include \"asm/nonmatchings/Overlays/walboss/walboss/WALBOSS_EjectEgg.s\"\n"
    ".set reorder\n"
    ".set at\n"
);

__asm__(
    ".section .text\n.set noat\n.set noreorder\n.globl WALBOSS_BirthEggEntry\n.type WALBOSS_BirthEggEntry, @function\nWALBOSS_BirthEggEntry:\n"
    ".include \"asm/nonmatchings/Overlays/walboss/walboss/WALBOSS_BirthEggEntry.s\"\n.set reorder\n.set at\n"
);

__asm__(
    ".section .text\n.set noat\n.set noreorder\n.globl WALBOSS_BirthEgg\n.type WALBOSS_BirthEgg, @function\nWALBOSS_BirthEgg:\n"
    ".include \"asm/nonmatchings/Overlays/walboss/walboss/WALBOSS_BirthEgg.s\"\n.set reorder\n.set at\n"
);

__asm__(".section .text\n.set noat\n.set noreorder\n.globl WALBOSS_CorrectLookAT\n.type WALBOSS_CorrectLookAT, @function\nWALBOSS_CorrectLookAT:\n.include \"asm/nonmatchings/Overlays/walboss/walboss/WALBOSS_CorrectLookAT.s\"\n.set reorder\n.set at\n");

__asm__(".section .text\n.set noat\n.set noreorder\n.globl WALBOSS_LegHurtEntry\n.type WALBOSS_LegHurtEntry, @function\nWALBOSS_LegHurtEntry:\n.include \"asm/nonmatchings/Overlays/walboss/walboss/WALBOSS_LegHurtEntry.s\"\n.set reorder\n.set at\n");

__asm__(".section .text\n.set noat\n.set noreorder\n.globl WALBOSS_LegHurt\n.type WALBOSS_LegHurt, @function\nWALBOSS_LegHurt:\n.include \"asm/nonmatchings/Overlays/walboss/walboss/WALBOSS_LegHurt.s\"\n.set reorder\n.set at\n");

__asm__(".section .text\n.set noat\n.set noreorder\n.globl WALBOSS_HitByEggEntry\n.type WALBOSS_HitByEggEntry, @function\nWALBOSS_HitByEggEntry:\n.include \"asm/nonmatchings/Overlays/walboss/walboss/WALBOSS_HitByEggEntry.s\"\n.set reorder\n.set at\n");

__asm__(".section .text\n.set noat\n.set noreorder\n.globl WALBOSS_HitByEgg\n.type WALBOSS_HitByEgg, @function\nWALBOSS_HitByEgg:\n.include \"asm/nonmatchings/Overlays/walboss/walboss/WALBOSS_HitByEgg.s\"\n.set reorder\n.set at\n");

__asm__(".section .text\n.set noat\n.set noreorder\n.globl WALBOSS_HandleThorn\n.type WALBOSS_HandleThorn, @function\nWALBOSS_HandleThorn:\n.include \"asm/nonmatchings/Overlays/walboss/walboss/WALBOSS_HandleThorn.s\"\n.set reorder\n.set at\n");

__asm__(".section .text\n.set noat\n.set noreorder\n.globl WALBOSS_Collide\n.type WALBOSS_Collide, @function\nWALBOSS_Collide:\n.include \"asm/nonmatchings/Overlays/walboss/walboss/WALBOSS_Collide.s\"\n.set reorder\n.set at\n");

__asm__(".section .text\n.set noat\n.set noreorder\n.globl WALBOSS_Message\n.type WALBOSS_Message, @function\nWALBOSS_Message:\n.include \"asm/nonmatchings/Overlays/walboss/walboss/WALBOSS_Message.s\"\n.set reorder\n.set at\n");

__asm__(".section .text\n.set noat\n.set noreorder\n.globl WALBOSS_Query\n.type WALBOSS_Query, @function\nWALBOSS_Query:\n.include \"asm/nonmatchings/Overlays/walboss/walboss/WALBOSS_Query.s\"\n.set reorder\n.set at\n");

INCLUDE_RODATA("asm/nonmatchings/Overlays/walboss/walboss", D_88000000);

INCLUDE_RODATA("asm/nonmatchings/Overlays/walboss/walboss", D_88000020);

INCLUDE_RODATA("asm/nonmatchings/Overlays/walboss/walboss", D_8800002C);

__asm__(".section .text\n.set noat\n.set noreorder\n.globl WALBOSS_Init\n.type WALBOSS_Init, @function\nWALBOSS_Init:\n.include \"asm/nonmatchings/Overlays/walboss/walboss/WALBOSS_Init.s\"\n.set reorder\n.set at\n");

__asm__(".section .text\n.set noat\n.set noreorder\n.globl WALBOSS_CleanUp\n.type WALBOSS_CleanUp, @function\nWALBOSS_CleanUp:\n.include \"asm/nonmatchings/Overlays/walboss/walboss/WALBOSS_CleanUp.s\"\n.set reorder\n.set at\n");

__asm__(".section .text\n.set noat\n.set noreorder\n.globl WALBOSS_DamageEffect\n.type WALBOSS_DamageEffect, @function\nWALBOSS_DamageEffect:\n.include \"asm/nonmatchings/Overlays/walboss/walboss/WALBOSS_DamageEffect.s\"\n.set reorder\n.set at\n");

__asm__(".section .text\n.set noat\n.set noreorder\n.globl WALBOSS_HandleFade\n.type WALBOSS_HandleFade, @function\nWALBOSS_HandleFade:\n.include \"asm/nonmatchings/Overlays/walboss/walboss/WALBOSS_HandleFade.s\"\n.set reorder\n.set at\n");

INCLUDE_ASM("asm/nonmatchings/Overlays/walboss/walboss", WALBOSS_IdleEntry);

INCLUDE_ASM("asm/nonmatchings/Overlays/walboss/walboss", WALBOSS_Idle);

INCLUDE_ASM("asm/nonmatchings/Overlays/walboss/walboss", WALBOSS_NoticeEntry);

INCLUDE_ASM("asm/nonmatchings/Overlays/walboss/walboss", WALBOSS_Notice);

INCLUDE_ASM("asm/nonmatchings/Overlays/walboss/walboss", WALBOSS_CombatEntry);

INCLUDE_ASM("asm/nonmatchings/Overlays/walboss/walboss", WALBOSS_Combat);

INCLUDE_ASM("asm/nonmatchings/Overlays/walboss/walboss", WALBOSS_AttackEntry);

__asm__(".section .text\n.set noat\n.set noreorder\n.globl WALBOSS_Attack\n.type WALBOSS_Attack, @function\nWALBOSS_Attack:\n.include \"asm/nonmatchings/Overlays/walboss/walboss/WALBOSS_Attack.s\"\n.set reorder\n.set at\n");

__asm__(".section .text\n.set noat\n.set noreorder\n.globl WALBOSS_HitEntry\n.type WALBOSS_HitEntry, @function\nWALBOSS_HitEntry:\n.include \"asm/nonmatchings/Overlays/walboss/walboss/WALBOSS_HitEntry.s\"\n.set reorder\n.set at\n");

__asm__(".section .text\n.set noat\n.set noreorder\n.globl WALBOSS_Hit\n.type WALBOSS_Hit, @function\nWALBOSS_Hit:\n.include \"asm/nonmatchings/Overlays/walboss/walboss/WALBOSS_Hit.s\"\n.set reorder\n.set at\n");

__asm__(".section .text\n.set noat\n.set noreorder\n.globl WALBOSS_DoNothingEntry\n.type WALBOSS_DoNothingEntry, @function\nWALBOSS_DoNothingEntry:\n.include \"asm/nonmatchings/Overlays/walboss/walboss/WALBOSS_DoNothingEntry.s\"\n.set reorder\n.set at\n");

void WALBOSS_DoNothing() { };

__asm__(".section .text\n.set noat\n.set noreorder\n.globl WALBOSS_DeadEntry\n.type WALBOSS_DeadEntry, @function\nWALBOSS_DeadEntry:\n.include \"asm/nonmatchings/Overlays/walboss/walboss/WALBOSS_DeadEntry.s\"\n.set reorder\n.set at\n");

__asm__(".section .text\n.set noat\n.set noreorder\n.globl WALBOSS_Dead\n.type WALBOSS_Dead, @function\nWALBOSS_Dead:\n.include \"asm/nonmatchings/Overlays/walboss/walboss/WALBOSS_Dead.s\"\n.set reorder\n.set at\n");

INCLUDE_RODATA("asm/nonmatchings/Overlays/walboss/walboss", func_88000060);

#else 

void WALBOSS_SetSegmentYaw(void) { };   

void WALBOSS_InterpSegmentYaw(void) { };

void WALBOSS_TurnToPosition(void) { };  

void WALBOSS_ChooseAttack(void) { };   

void WALBOSS_ShouldIAttack(void) { };  

void WALBOSS_MessageToLegs(void) { };   

void WALBOSS_LivingLegs(void) { };   

void WALBOSS_FindEgg(void) { };   

void WALBOSS_CreateEgg(void) { };  

void WALBOSS_EjectEgg(void) { }; 

void WALBOSS_BirthEggEntry(void) { };  

void WALBOSS_BirthEgg(void) { };    

void WALBOSS_CorrectLookAT(void) { };   

void WALBOSS_LegHurtEntry(void) { };   

void WALBOSS_LegHurt(void) { };     

void WALBOSS_HitByEggEntry(void) { };  

void WALBOSS_HitByEgg(void) { };    

void WALBOSS_HandleThorn(void) { };  

void WALBOSS_Collide(void) { };   

void WALBOSS_Message(void) { };   

void WALBOSS_Query(void) { };   

void WALBOSS_Init(void) { };     

void WALBOSS_CleanUp(void) { };  

void WALBOSS_DamageEffect(void) { };  

void WALBOSS_HandleFade(void) { };    

void WALBOSS_IdleEntry(void) { };     

void WALBOSS_Idle(void) { };           

void WALBOSS_NoticeEntry(void) { };    

void WALBOSS_Notice(void) { };     

void WALBOSS_CombatEntry(void) { };   

void WALBOSS_Combat(void) { };     

void WALBOSS_AttackEntry(void) { }; 

void WALBOSS_Attack(void) { };  

void WALBOSS_HitEntry(void) { };     

void WALBOSS_Hit(void) { };         

void WALBOSS_DoNothingEntry(void) { };  

void WALBOSS_DoNothing(void) { };     

void WALBOSS_DeadEntry(void) { };  

void WALBOSS_Dead(void) { };        

#endif
